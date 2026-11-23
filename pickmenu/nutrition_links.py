"""검수한 영양 연결을 실행 카탈로그 사본에 화면 표시용으로 붙인다.

    python -X utf8 nutrition_links.py --catalog 원본.sqlite3 --approvals nutrition_approvals.json \
        --food-csv "식품의약품안전처_통합식품영양성분정보(음식).csv" --research 대표음식_조사.json \
        --output 새카탈로그.sqlite3

- 원본 카탈로그는 읽기만 한다. 출력은 다른 경로여야 한다.
- 승인 목록(`nutrition_approvals.json`)의 approved만 붙인다. K-FIND 음식 원본에서 식품코드·기준량·수치를 다시
  읽고, 승인 목록의 종류 조건(일반 음식은 가정식·외식 분석, 제품은 같은 업체명)이 맞지 않으면 멈춘다.
- 새 값은 `references`에만 더한다. 추천 점수가 쓰는 `nutrition`은 바꾸지 않는다.
- 100g·100mL 원문 단위를 그대로 두고 1인분으로 환산하지 않는다. 빈 수치는 null이다.
- 같은 메뉴에 같은 식품코드(옵션)가 이미 있으면 다시 붙이지 않는다. 같은 입력으로 다시 실행해도 결과가 같다.
- 식약처 외식 자료집의 동명 음식 조사 제공량은 `survey_servings`로 따로 붙인다(매장 1인분 아님).
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

FIELDS = ("에너지(kcal)", "단백질(g)", "탄수화물(g)", "지방(g)", "나트륨(mg)")
GENERAL_ORIGINS = re.compile(r"^(가정식|외식)\(분석")
DETAIL_URL = "https://various.foodsafetykorea.go.kr/nutrient/general/food/detail.do?searchFoodCd="
METHOD = "사람 검수 연결 (2026-11-23)"
STATUS = {"general": "일반 음식 참고값 (검수 연결)", "product": "해당 제품 기준 (검수 연결)",
          "product_options": "해당 브랜드 제품 · 옵션별 값 (옵션 미확정)"}


def number(text: str | None) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


def basis_of(text: str) -> tuple[int, str]:
    match = re.fullmatch(r"\s*100\s*(g|ml)\s*", text or "", re.I)
    if not match:
        raise ValueError(f"100g·100mL가 아닌 기준량: {text!r}")
    return 100, "g" if match[1].lower() == "g" else "mL"


def food_reference(row: dict[str, str], approval: dict[str, Any]) -> dict[str, Any]:
    kind = approval["kind"]
    origin = row.get("식품기원명", "")
    if kind == "general" and not GENERAL_ORIGINS.match(origin):
        raise ValueError(f"{approval['standard_menu_id']}: 일반 음식 참고값은 분석 음식만 씁니다({origin}).")
    if kind == "product" and row.get("업체명", "").strip() != approval["brand"]:
        raise ValueError(f"{approval['standard_menu_id']}: 업체명이 승인 브랜드와 다릅니다.")
    basis, unit = basis_of(row["영양성분함량기준량"])
    ref: dict[str, Any] = {
        "name": row["식품명"], "food_id": row["식품코드"], "basis": basis, "unit": unit,
        "values": [number(row.get(f)) for f in FIELDS], "original_basis": row["영양성분함량기준량"],
        "serving_reference": None, "source": row.get("출처명") or "식품의약품안전처",
        "source_url": DETAIL_URL + row["식품코드"], "method": METHOD, "kind": kind,
        "origin": origin, "review_note": approval["reason"],
        "confidence": "일반 음식 참고값 · 매장 레시피·제공량 미확인" if kind == "general"
        else "해당 제품 기준 · 업체 제공 영양정보",
    }
    if kind == "product":
        ref.update(brand=approval["brand"], product_name=approval["product_name"])
    return ref


def option_references(approval: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for option in approval["options"]:
        refs.append({
            "name": f"{approval['product_name']} ({option['option']})", "food_id": None,
            "basis": approval["basis"], "unit": approval["unit"],
            "values": [option["energy_kcal"], option["protein_g"], option["carbohydrate_g"],
                       option["fat_g"], option["sodium_mg"]],
            "extra": {"당류": [option["sugar_g"], "g"], "포화지방": [option["saturated_fat_g"], "g"]},
            "original_basis": f"{approval['basis']}{approval['unit']}", "serving_reference": None,
            "source": approval["source"], "source_url": approval["source_url"], "method": METHOD,
            "kind": "product_option", "option": option["option"], "brand": approval["brand"],
            "product_name": approval["product_name"], "review_note": approval["reason"],
            "confidence": "해당 브랜드 제품 · 판매 옵션(뼈·순살) 미확정",
        })
    return refs


def survey_servings(research: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """대표 음식 이름 → 식약처 외식 자료집의 동명 음식 조사 제공량."""
    result: dict[str, list[dict[str, Any]]] = {}
    for food in research.get("메뉴별 자료", []):
        items = []
        for entry in food.get("식약처 외식 자료집 제공량", []):
            grams = entry.get("자료집 제공량_g")
            if not grams:
                continue
            items.append({"food": entry["음식명"], "grams": grams, "source": entry["자료명"],
                          "page": entry.get("PDF 페이지"), "source_url": entry["출처 URL"],
                          "nutrients": entry.get("자료집 제공량당 영양", {})})
        if items:
            result[food["대표 음식"]] = items
    return result


def apply(catalog: Path, approvals_path: Path, food_csv: Path, research_path: Path | None,
          output: Path) -> dict[str, Any]:
    if catalog.resolve() == output.resolve():
        raise ValueError("원본 보존을 위해 출력 카탈로그는 다른 경로여야 합니다.")
    approvals = json.loads(approvals_path.read_text(encoding="utf-8"))["records"]
    wanted = {a["food_code"] for a in approvals if a["decision"] == "approved" and a.get("food_code")}
    foods: dict[str, dict[str, str]] = {}
    with food_csv.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("식품코드") in wanted:
                if row["식품코드"] in foods:
                    raise ValueError(f"식품코드 중복: {row['식품코드']}")
                foods[row["식품코드"]] = row
    if missing := wanted - foods.keys():
        raise ValueError(f"원본에 없는 식품코드: {sorted(missing)}")
    servings = survey_servings(json.loads(research_path.read_text(encoding="utf-8"))) if research_path else {}

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".sqlite3", delete=False) as temp:
        temporary = Path(temp.name)
    counts: Counter[str] = Counter()
    log: list[tuple[str, str, str, str, str]] = []
    try:
        with closing(sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True)) as src, closing(
                sqlite3.connect(temporary)) as db:
            src.backup(db)
            menus = {mid: json.loads(data) for mid, data in db.execute("SELECT id,data FROM catalog")}
            counts["menus"] = len(menus)
            counts["with_references_before"] = sum(bool(m["references"]) for m in menus.values())
            scored_before = {mid: m["nutrition"] for mid, m in menus.items()}
            changed: set[str] = set()
            for approval in approvals:
                mid = approval["standard_menu_id"]
                if approval["decision"] != "approved":
                    counts["held"] += 1
                    log.append((mid, "hold", approval.get("kind") or "", "", approval["reason"]))
                    continue
                menu = menus.get(mid)
                if menu is None:
                    raise ValueError(f"카탈로그에 없는 표준 메뉴 ID: {mid}")
                if approval["kind"] == "product_options":
                    refs = option_references(approval)
                else:
                    refs = [food_reference(foods[approval["food_code"]], approval)]
                keys = {(r.get("food_id"), r.get("option")) for r in menu["references"]}
                added = [r for r in refs if (r.get("food_id"), r.get("option")) not in keys]
                if not added:
                    counts["already_linked"] += 1
                    log.append((mid, "unchanged", approval["kind"], approval.get("food_code") or "", "이미 연결됨"))
                    continue
                action = "add" if not menu["references"] else "add_beside_existing"
                menu["references"] = [*menu["references"], *added]
                if action == "add":
                    menu["nutrition_status"] = STATUS[approval["kind"]]
                counts[f"{action}_{approval['kind']}"] += 1
                changed.add(mid)
                log.append((mid, action, approval["kind"], approval.get("food_code") or "", approval["reason"]))
            for mid, menu in menus.items():
                before = menu.pop("survey_servings", None)
                if items := servings.get(menu["components"][0]):
                    menu["survey_servings"] = items
                    counts["survey_serving_menus"] += 1
                if before != menu.get("survey_servings"):
                    changed.add(mid)
            for mid in changed:
                db.execute("UPDATE catalog SET data=? WHERE id=?", (json.dumps(menus[mid], ensure_ascii=False), mid))
            if any(menus[mid]["nutrition"] != scored_before[mid] for mid in menus):
                raise AssertionError("추천 점수용 nutrition이 바뀌었습니다.")
            db.execute("DROP TABLE IF EXISTS nutrition_link_log")
            db.execute("CREATE TABLE nutrition_link_log(menu_id TEXT, action TEXT, kind TEXT, food_code TEXT, "
                       "reason TEXT)")
            db.executemany("INSERT INTO nutrition_link_log VALUES(?,?,?,?,?)", log)
            counts["with_references_after"] = sum(bool(m["references"]) for m in menus.values())
            db.commit()
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("출력 DB 무결성 검사 실패")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    with closing(sqlite3.connect(output.as_uri() + "?mode=ro", uri=True)) as db:
        digest = hashlib.sha256()
        for mid, data in db.execute("SELECT id,data FROM catalog ORDER BY id"):
            digest.update(f"{mid}\t{data}\n".encode())
    report = {"counts": dict(counts), "catalog_logical_sha256": digest.hexdigest(),
              "approved": [row for row in log if row[1] != "hold"],
              "held": [row[0] for row in log if row[1] == "hold"],
              "policy": "새 값은 references와 survey_servings에만 더한다. nutrition(추천 점수)은 바꾸지 않는다."}
    output.with_suffix(".nutrition-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                           encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--approvals", type=Path, default=Path(__file__).with_name("nutrition_approvals.json"))
    parser.add_argument("--food-csv", type=Path, required=True)
    parser.add_argument("--research", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = apply(args.catalog, args.approvals, args.food_csv, args.research, args.output)
    print(json.dumps(result["counts"], ensure_ascii=False))
