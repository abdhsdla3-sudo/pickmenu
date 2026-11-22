"""이야기 추천 통합 회귀 시험.

기록된 AI 원본 조건(ai_runs)을 그대로 넣고 서버 규칙(conditions → recommend_relaxed → reply)만 다시 돌려
채점한다. 모델 응답이 흔들리지 않으므로 코드 변경 효과만 보인다. 기준 점수(story_baseline.json)에서 맞던
문장이 틀리면 새로 틀린 문장으로 보여 주고 종료 코드 1을 낸다.

pickmenu 폴더에서 실행한다(카탈로그가 필요하다).
    PYTHONPATH=. .venv/bin/python -X utf8 eval/story_regression.py
    PYTHONPATH=. .venv/bin/python -X utf8 eval/story_regression.py --model pickmenu-llm:8b-ft-q8
    PYTHONPATH=. .venv/bin/python -X utf8 eval/story_regression.py --write-baseline   # 기준 점수 갱신
    PYTHONPATH=. .venv/bin/python -X utf8 eval/story_regression.py --live            # 실제 AI 호출(오래 걸림)
    PYTHONPATH=. .venv/bin/python -X utf8 eval/story_regression.py --live --prompt v2 --only SIT,ROL
        # v2 출력 형식으로 일부 시험셋만(id 앞부분). 실제 AI 출력은 결과.jsonl의 ai_output에 남는다.
"""
import argparse
import csv
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import llm
import recommend as reco
import server

HERE = Path(__file__).resolve().parent
VERSION_FILE = HERE.parents[1] / "VERSION"
FIELDS = ("query", "meal_category", "spice", "soup", "goal", "taste", "allergy", "exclude", "diet", "district")
KEYS = (*FIELDS, "avoid_menus", "exclude_categories", "exclude_groups")
DEFAULT = {"diet": "general", "goal": "balance"}


def terms(value: Any) -> set[str]:
    items = value if isinstance(value, list) else str(value or "").split(",")
    return {item.strip() for item in items if item and item.strip()}


def field_ok(field: str, want: Any, got: dict[str, str], base: dict[str, str], expect: dict[str, Any],
             foods: dict[str, set[str]], families: list[str], listed: bool = False) -> bool:
    """listed: 기대에 적은 칸만 보는 시험셋. 거기서 빈 값("")은 '조건을 풀어야 함'이라 저장 프로필 값이면 틀리다."""
    have = got.get(field, "") or ""
    if field == "taste":
        return terms(want) == terms(have)
    if field in ("allergy", "exclude"):
        return (want in terms(have)) if want else not (terms(have) - terms(base.get(field)))
    if field == "avoid_menus":
        return terms(want) == terms(have) if want else not (terms(have) - terms(base.get(field)))
    if field == "query":
        if want:
            # 같은 이름의 음식군으로 받았거나(죽 → 음식군 죽) 그 메뉴가 1위면 결과가 같다.
            return have == want or (not have and (got.get("meal_category") == want or families[:1] == [want]))
        category = expect.get("meal_category")
        return not have or bool(isinstance(category, str) and category and category in foods.get(have, set()))
    if field == "meal_category":
        if want:
            return have == want or (not have and want in foods.get(got.get("query", ""), set()))
        named_value = expect.get("query")
        named = named_value if isinstance(named_value, str) else ""
        # 메뉴를 검색하면서 그 메뉴가 속한 음식군을 같이 붙인 경우(피자 + 음식군 피자)는 결과가 같다.
        return not have or have in foods.get(got.get("query", ""), set()) or have in foods.get(named, set()) \
            or have == named
    default = DEFAULT.get(field, "") if listed else base.get(field) or DEFAULT.get(field, "")
    return (have or DEFAULT.get(field, "")) == (want or default)


def judge(case: dict[str, Any], got: dict[str, str], families: list[str], foods: dict[str, set[str]]) -> list[str]:
    expect, base = case["expect"], {**DEFAULT, **case["profile"]}
    fields = ([f for f in FIELDS if f not in case.get("skip", [])] if case["mode"] == "strict"
              else [k for k in expect if not k.endswith("_has")])
    wrong = []
    for field in fields:
        want = expect.get(field, "")
        options = want if isinstance(want, list) else [want]
        if not any(field_ok(field, w, got, base, expect, foods, families, case["mode"] == "listed")
                   for w in options):
            wrong.append(f"{field}(기대 {want or '없음'} / 실제 {got.get(field) or '없음'})")
    for key in (k for k in expect if k.endswith("_has")):
        field = key[:-4]
        if missing := [item for item in expect[key] if item and item not in terms(got.get(field))]:
            wrong.append(f"{field}({','.join(missing)} 빠짐)")
    if bad := [f for f in families if f in set(case.get("not_families", []))]:
        wrong.append(f"나오면 안 되는 메뉴({','.join(bad)})")
    if bad := [f for f in families if any(term in f for term in case.get("block_terms", []))]:
        wrong.append(f"빼야 할 재료가 든 메뉴({','.join(bad)})")
    if case.get("nonempty") and not families:
        wrong.append("추천 0개")
    if case.get("no_results") and families:
        wrong.append(f"추천이 없어야 함({','.join(families)})")
    return wrong


def run_case(case: dict[str, Any], model: str, live: bool, menus: list[dict[str, Any]], cats: list[str],
             foods: dict[str, set[str]]) -> dict[str, Any]:
    runs = case.get("ai_runs") or []
    run = next((r for r in runs if r["model"] == model), runs[0] if runs else None)
    profile = {**DEFAULT, "district": "", **case["profile"]}
    payload = {"messages": case["messages"], "profile": profile}
    calls = iter([dict(run["output"])] if run else [])

    def recorded(*_: Any, **__: Any) -> dict[str, Any]:
        try:
            return next(calls)
        except StopIteration:
            raise llm.Unavailable("기록 없음") from None

    seen: list[dict[str, Any]] = []

    def live_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        output = original(*args, **kwargs)
        seen.append(dict(output))
        return output

    original = llm.generate
    started = time.time()
    record: dict[str, Any] = {"id": case["id"], "source": case["source"], "kind": case["kind"],
                              "messages": case["messages"], "profile": profile, "expect": case["expect"],
                              "ai_model": model if live else (run or {}).get("model", ""),
                              "ai_output": None if live else (run or {}).get("output")}
    try:
        with patch.object(llm, "generate", live_call if live else recorded):
            parsed, history = llm.conditions(payload, cats, foods)
            result, _ = reco.recommend_relaxed(menus, parsed, [], conversation=True)
            saved = reco.Profile.parse(payload["profile"], cats)
            reply = llm.reply(result, history, taste=parsed.taste, profile=parsed, saved=saved)
        got = {k: getattr(parsed, k) for k in KEYS if getattr(parsed, k)}
        families = [f["name"] for f in result.get("families", [])[:5]]
        wrong = judge(case, got, families, foods)
        record.update(conditions=got, families=families, relaxed=result.get("relaxed", []), reply=reply, wrong=wrong)
    except (ValueError, llm.Unavailable) as error:
        record.update(conditions={}, families=[], relaxed=[], reply="", wrong=[f"오류({error})"])
    if live:
        record["ai_output"] = seen[0] if seen else None
    record["sec"] = round(time.time() - started, 2)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cases", default=str(HERE / "story_regression.jsonl"))
    parser.add_argument("--baseline", default=str(HERE / "story_baseline.json"))
    parser.add_argument("--model", default="pickmenu-llm:8b", help="기록된 AI 응답 중 고를 모델(없으면 첫 기록)")
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "pickmenu-story-regression"))
    parser.add_argument("--write-baseline", action="store_true", help="이번 결과를 기준 점수로 저장")
    parser.add_argument("--live", action="store_true", help="기록 대신 실제 AI를 부른다(PICKMENU_LLM_URL 필요)")
    parser.add_argument("--prompt", choices=("v1", "v2"), help="실제 AI에 쓸 출력 형식(PICKMENU_LLM_PROMPT)")
    parser.add_argument("--only", default="", help="이 id 앞부분(쉼표로 여럿)으로 시작하는 문장만 실행")
    args = parser.parse_args()
    if args.prompt:
        os.environ["PICKMENU_LLM_PROMPT"] = args.prompt
    if args.live:
        os.environ["PICKMENU_LLM_MODEL"] = args.model

    # 시험 모듈로 불러올 때는 환경을 바꾸지 않는다(다른 시험이 기본 카탈로그 경로를 쓴다).
    os.environ.setdefault("PICKMENU_CATALOG_PATH", os.path.expanduser("~/.local/share/pickmenu/catalog-v03.sqlite3"))
    menus = server.prepared_menus()
    cats, foods = server.categories(menus), server.foods(menus)
    with open(args.cases, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    if prefixes := tuple(p for p in args.only.split(",") if p):
        cases = [case for case in cases if case["id"].startswith(prefixes)]
    records = [run_case(case, args.model, args.live, menus, cats, foods) for case in cases]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "결과.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out / "채점결과.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "시험셋", "유형", "대화", "저장 프로필", "기대 조건", "AI 원본 조건", "서버 조건", "판정",
                    "틀린 점", "뺀 조건", "추천 대표 메뉴", "답변"])
        for r in records:
            w.writerow([r["id"], r["source"], r["kind"], " / ".join(r["messages"]),
                        json.dumps(r["profile"], ensure_ascii=False), json.dumps(r["expect"], ensure_ascii=False),
                        json.dumps(r["ai_output"], ensure_ascii=False), json.dumps(r["conditions"], ensure_ascii=False),
                        "틀림" if r["wrong"] else "맞음", " · ".join(r["wrong"]), "·".join(r["relaxed"]),
                        ", ".join(r["families"]), r["reply"]])

    version = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else ""
    per_source: dict[str, Counter[str]] = defaultdict(Counter)
    for r in records:
        per_source[r["source"]]["전체"] += 1
        per_source[r["source"]]["맞음"] += not r["wrong"]
        per_source[r["source"]]["추천 0개"] += not r["families"]
    passed = sorted(r["id"] for r in records if not r["wrong"])
    summary = {
        "버전": version, "모델": args.model, "실행": "실제 AI" if args.live else "기록 재생", "문장 수": len(records),
        "맞음": len(passed), "시험셋별": {k: dict(v) for k, v in per_source.items()},
        "틀린 항목 빈도": dict(Counter(w.split("(")[0] for r in records for w in r["wrong"]).most_common()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))

    baseline_path = Path(args.baseline)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {"runs": {}}
    before = baseline["runs"].get(args.model)
    status = 0
    if before and not args.live and not prefixes:
        old, now = set(before["passed"]), set(passed)
        regressed = sorted(old - now)
        fixed = sorted(now - old)
        print(f"\n기준({before['버전']}) 대비: 맞음 {len(old)} → {len(now)}, 새로 맞음 {len(fixed)}, 새로 틀림 {len(regressed)}")
        by_id = {r["id"]: r for r in records}
        for case_id in regressed:
            r = by_id[case_id]
            print(f"  새로 틀림 {case_id} {' / '.join(r['messages'])[:60]} | {' · '.join(r['wrong'])}")
        status = 1 if regressed else 0
    if args.write_baseline and not args.live and not prefixes:
        baseline["runs"][args.model] = {"버전": version, "맞음": len(passed), "passed": passed,
                                        "시험셋별": summary["시험셋별"]}
        baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"기준 점수 저장: {baseline_path}")
    print(f"자세한 결과: {out}")
    return status


if __name__ == "__main__":
    sys.exit(main())
