"""전체 자료를 검증하고 로컬 추천용 카탈로그를 만든다."""

import argparse
import csv
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from contextlib import closing
from typing import Any

from drive_data import DRIVE, NUTRIENTS, OFFICIAL, RECIPE, records, scan_drive
from raw import FIELDS, LOCAL, key, ordered_menus, scan
from recommend import COOKING_EFFECT, compact

CATALOG = LOCAL / "catalog.sqlite3"
FULL = "01_수원시_전체리뷰.csv"
STORES = "01_수원시_매장기준정보_2607-2609.xlsx"
MAP = "02_수원시_메뉴정규화_2607-2609.xlsx"
CLEAN = "03_수원시_리뷰정제_2607-2609.xlsx"
AGG = "05_수원시_매장메뉴집계_2607-2609.xlsx"
NUTR = "06_공공영양정보.xlsx"
NUTR_MAP = "07_메뉴영양정보_연결후보.xlsx"
TYPES = "메뉴정규화_최종/메뉴_음식종류.csv"
BETA = "메뉴_음식종류_영양결합.csv"
TYPE_NUTR = "음식종류_영양연결표.csv"
FOOD_FILE = "식품의약품안전처_통합식품영양성분정보(음식).csv"
FOOD_SOURCE = "식약처 K-FIND — 영양성분.zip!" + FOOD_FILE
SOURCE_URL = "https://various.foodsafetykorea.go.kr/nutrient/general/down/list.do"
COMPOUND = re.compile(r"[+＋&＆/]|세트|정식|콤보|선택|반반|\bSET\b|\s[x×]\s", re.IGNORECASE)
NOT_MEAL = {"음료", "카페음료", "디저트·빵", "사이드"}


def number(value: str) -> float | None:
    try:
        amount = float(value)
    except ValueError:
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def taste_tags(name: str, components: list[str], category: str) -> tuple[str, str]:
    signal = compact(" ".join((name, *components)))
    mild = re.search(r"안매운|맵지않|순한맛|간장맛", signal)
    hot = re.search(r"매운|매콤|불닭|얼큰|핫스파이시|매운맛", signal)
    spice = "mild" if mild else "hot" if hot else ""
    soup = "yes" if (category in {"찌개·탕", "국밥·해장국"} or
                     re.search(r"국밥|해장국|찌개|설렁탕|곰탕|갈비탕|우동|물냉면|짬뽕탕", signal)) else ""
    if re.search(r"국물없|비빔|볶음우동|볶음짬뽕", signal):
        soup = "no"
    elif not soup and (category in {"피자", "치킨", "샐러드·건강식", "버거·샌드위치", "밥·덮밥"}
                       or re.search(r"덮밥|볶음밥|김밥|주먹밥|돈까스|돈가스", signal)):
        soup = "no"
    return spice, soup


def reference(row: dict[str, str], index: int, official: dict[str, dict[str, str]],
              type_links: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    prefix = f"음식{index}_"
    code = row.get(prefix + "영양식품ID", "")
    food = official.get(code, {})
    component = row.get(f"음식{index}", "")
    type_link = type_links.get(component, {})
    basis = re.fullmatch(r"([0-9.]+)\s*g", food.get("영양성분함량기준량", ""), re.I)
    if (not basis or row.get(prefix + "자료파일") != FOOD_FILE
            or row.get(prefix + "표준화기준량") != "100"
            or row.get(prefix + "표준화단위") != "g"
            or type_link.get("영양식품ID") != code
            or row.get(prefix + "식품명") != food.get("식품명")):
        return None
    grams = float(basis[1])
    if grams <= 0:
        return None
    values = [number(row.get(prefix + "표준화" + field, "")) for field in NUTRIENTS]
    raw_values = [number(food.get(field, "")) for field in OFFICIAL]
    if any(actual is None or original is None or abs(actual - original * 100 / grams) > 0.011
           for actual, original in zip(values, raw_values)):
        return None
    return {"name": food["식품명"], "food_id": code, "basis": 100, "unit": "g", "values": values,
            "original_basis": food["영양성분함량기준량"],
            "serving_reference": food.get("1인(회)분량 참고량"),
            "source": food.get("출처명") or row.get(prefix + "출처명"),
            "source_url": SOURCE_URL, "source_file": FOOD_FILE, "source_row": int(food["_row"]),
            "method": row[prefix + "연결상태"], "confidence": "자동 연결 · 원수치 대조"}


def stage_evidence(src: sqlite3.Connection, db: sqlite3.Connection,
                   counts: Counter[str]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    stores = {r["매장ID"]: r.get("시군구", "").removeprefix("수원시 ").strip()
              for r in records(src, STORES, "매장기준정보")}
    representatives = {r["리뷰ID"] for r in records(src, CLEAN, "정제리뷰")
                       if r.get("완전중복대표행여부") in {"예", "1", "True"}}
    if not representatives:
        raise ValueError("완전중복 대표 리뷰가 없습니다.")
    db.execute("ATTACH DATABASE ? AS raw", (str(LOCAL / "raw.sqlite3"),))
    with (DRIVE / FULL).open(encoding="utf-8-sig", newline="") as stream:
        stage_reviews(db, csv.DictReader(stream), stores, representatives, counts)
    descriptions: dict[str, dict[str, str]] = {}
    for row in records(src, MAP, "메뉴연결"):
        mid = row.get("표준메뉴ID", "")
        if row.get("항목유형") not in {"음식", "음식·검토"} or not mid:
            counts["menu_link_excluded_role"] += 1
            continue
        inserted = db.execute("""
            INSERT OR IGNORE INTO trace
            SELECT ?, review_id, store_id, ?, ?, ?
            FROM review WHERE review_id=? AND store_id=? AND representative=1 AND raw_link=1
        """, (mid, int(row["메뉴순번"]), row["원본메뉴명"], row.get("검토사유", ""),
              row["리뷰ID"], row["매장ID"])).rowcount
        counts["menu_link_included" if inserted else "menu_link_duplicate_or_unlinked"] += 1
        if inserted:
            descriptions.setdefault(mid, row)
    counts["representative_reviews"] = len(representatives)
    return descriptions, stores


def stage_reviews(db: sqlite3.Connection, rows: Iterable[dict[str, str]], stores: dict[str, str],
                  representatives: set[str], counts: Counter[str]) -> None:
    for row in rows:
        rid, sid = row["review_row_id"], row["store_id"]
        district = stores.get(sid)
        if not district or district != row.get("district"):
            counts["review_store_mismatch"] += 1
            continue
        stamp = row["created_at_KST"]
        if not "2026-07-01" <= stamp < "2026-10-01":
            counts["review_outside_period"] += 1
            continue
        source_row = int(row["source_review_row"])
        raw = db.execute("""SELECT district,platform,menus,fingerprint
                         FROM raw.raw_review INDEXED BY raw_valid_row WHERE file=? AND valid_row=?""",
                         (row["source_file"], source_row)).fetchone()
        try:
            menu_values = ordered_menus(row["menus"]) if row.get("menus", "").strip() else []
        except ValueError:
            menu_values = []
            counts["drive_json_error"] += 1
        matched = bool(raw and raw[0] == district and raw[1] == row["platform"]
                       and json.loads(raw[2]) == menu_values)
        if matched and raw:
            matched = raw[3] == key(*(row[f] for f in FIELDS))
            counts["raw_full_fingerprint_equal" if matched else "raw_full_fingerprint_diff"] += 1
        counts["raw_link_ok" if matched else "raw_link_mismatch"] += 1
        db.execute("INSERT INTO review VALUES(?,?,?,?,?,?,?,?)",
                   (rid, sid, district, row["platform"], row["source_file"], source_row,
                    int(rid in representatives), int(matched)))


def validate_links(src: sqlite3.Connection, db: sqlite3.Connection,
                   stores: dict[str, str]) -> dict[str, Any]:
    review_ids = {r[0] for r in db.execute("SELECT review_id FROM review")}
    menu_ids = {r["표준메뉴ID"] for r in records(src, NUTR_MAP, "메뉴영양연결후보")}
    public_ids = {r["영양식품ID"] for r in records(src, NUTR, "음식영양자료")}
    recipe_ids = {r["레시피ID"] for r in records(src, RECIPE, "레시피자료")}
    ids = {"리뷰ID": review_ids, "review_row_id": review_ids, "매장ID": stores, "store_id": stores,
           "표준메뉴ID": menu_ids, "메뉴ID": menu_ids, "영양식품ID": public_ids,
           "선택영양식품ID": public_ids, "참고레시피ID": recipe_ids, "레시피ID": recipe_ids,
           "RCP_SEQ": recipe_ids}
    linked: dict[str, Counter[str]] = defaultdict(Counter)
    for f, s, payload in src.execute("SELECT file,sheet,data FROM records WHERE file NOT LIKE '%!%'"):
        row = json.loads(payload)
        report = linked[f + ("!" + s if s else "")]
        for field in row.keys() & ids.keys():
            report[field + ("_일치" if row[field] in ids[field] else "_미일치")] += 1
        for field in ("전체감성", "최종리뷰감성", "평가속성", "비교결과"):
            if field in row:
                report[field + "=" + row[field]] += 1
    for f in (DRIVE / "06_공공영양정보_API원본.csv",):
        with f.open(encoding="utf-8-sig", newline="") as stream:
            report = linked[f.name]
            for row in csv.DictReader(stream):
                report["FOOD_CD_일치" if row.get("FOOD_CD") in public_ids else "FOOD_CD_미일치"] += 1
    return {f: dict(c) for f, c in sorted(linked.items())}


def recipe_cooking_methods(src: sqlite3.Connection) -> dict[str, str]:
    """검토 후 연결된 공개 레시피의 조리방법을 표준 메뉴 ID로 조회한다."""
    methods = {row["레시피ID"]: row.get("조리방법", "").strip()
               for row in records(src, RECIPE, "레시피자료") if row.get("레시피ID")}
    links: dict[str, set[str]] = defaultdict(set)
    for row in records(src, RECIPE, "메뉴레시피검색"):
        if (row.get("표준메뉴ID") and row.get("레시피연결승인여부") == "1"
                and row.get("참고연결검토상태") == "AI본문검토·유형참고연결"):
            links[row["표준메뉴ID"]].add(row.get("참고레시피ID", ""))
    return {mid: method for mid, recipes in links.items()
            if len(recipes) == 1 and (method := methods.get(next(iter(recipes)), "")) in COOKING_EFFECT}


def build() -> None:
    with tempfile.NamedTemporaryFile(dir=LOCAL, suffix=".sqlite3", delete=False) as temp:
        temporary = temp.name
    counts: Counter[str] = Counter()
    try:
        with closing(sqlite3.connect(LOCAL / "drive.sqlite3", timeout=120)) as src, closing(
                sqlite3.connect(temporary)) as db:
            db.executescript("""
                CREATE TABLE review(
                    review_id TEXT PRIMARY KEY, store_id TEXT, district TEXT, platform TEXT,
                    source_file TEXT, source_row INTEGER, representative INTEGER, raw_link INTEGER
                ) WITHOUT ROWID;
                CREATE TABLE trace(
                    menu_id TEXT, review_id TEXT, store_id TEXT, menu_position INTEGER,
                    original_name TEXT, review_note TEXT, PRIMARY KEY(menu_id,review_id)
                ) WITHOUT ROWID;
                CREATE TABLE catalog(id TEXT PRIMARY KEY, data TEXT) WITHOUT ROWID;
            """)
            descriptions, stores = stage_evidence(src, db, counts)
            aggregate = {(r["매장ID"], r["메뉴ID"]): (int(r["관련리뷰수"]), r.get("음식분류", "미상"))
                         for r in records(src, AGG, "매장메뉴요약")}
            actual = {(sid, mid): size for sid, mid, size in db.execute(
                "SELECT store_id,menu_id,count(*) FROM trace GROUP BY store_id,menu_id")}
            counts["store_menu_aggregate_exact"] = sum(actual.get(k) == n for k, (n, _) in aggregate.items())
            counts["store_menu_aggregate_diff"] = len(aggregate) - counts["store_menu_aggregate_exact"]
            counts["store_menu_trace_only"] = len(actual.keys() - aggregate.keys())
            aggregate_differences = Counter(group for k, (n, group) in aggregate.items()
                                            if actual.get(k) != n)
            region_data: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
            for mid, district, count, locations in db.execute("""
                SELECT menu_id,district,count(*),count(DISTINCT trace.store_id)
                FROM trace JOIN review USING(review_id) GROUP BY menu_id,district
                UNION ALL
                SELECT menu_id,'수원시',count(*),count(DISTINCT store_id) FROM trace GROUP BY menu_id
            """):
                region_data[mid][district] = {"reviews": count, "stores": locations}
            official = {r["식품코드"]: r for r in records(src, FOOD_SOURCE) if r.get("식품코드")}
            type_links = {r["음식종류"]: r for r in records(src, TYPE_NUTR)}
            beta = {r["표준메뉴ID"]: r for r in records(src, BETA)}
            provided = {r["표준메뉴ID"]: r for r in records(src, NUTR_MAP, "메뉴영양연결후보")}
            cooking_methods = recipe_cooking_methods(src)
            for row in records(src, TYPES):
                mid = row["표준메뉴ID"]
                category = row.get("그룹", "")
                components = [row[k] for k in ("음식1", "음식2") if row.get(k)]
                name = row["표준메뉴명"]
                if (not category or category in NOT_MEAL or not components or not region_data.get(mid)
                        or components[0] in {"비음식", "검토필요"}):
                    counts["catalog_excluded_nonmeal_or_no_trace"] += 1
                    continue
                compound = row.get("구성") != "단품" or bool(COMPOUND.search(name))
                source = beta.get(mid, {})
                refs = [ref for i in (1, 2) if (ref := reference(source, i, official, type_links))]
                counts["beta_reference_rows" if refs else "no_beta_reference_rows"] += 1
                counts["beta_reference_rejected"] += sum(bool(source.get(f"음식{i}_영양식품ID"))
                                                         for i in (1, 2)) - len(refs)
                nutrition = refs[0] if refs and not compound and len(components) == 1 else None
                counts["scored_nutrition" if nutrition else "nutrition_missing_or_component_only"] += 1
                candidate = provided.get(mid, {})
                spice, soup = taste_tags(name, components, category)
                menu = {"standard_menu_id": mid, "name": name, "category": category,
                        "components": components, "compound": compound, "spice": spice, "soup": soup,
                        "regions": region_data[mid], "nutrition": nutrition, "references": refs,
                        "nutrition_status": ("공개 일반음식 참고" if refs else
                                             "검토 필요" if candidate.get("선택영양식품ID") else "자료 없음"),
                        "mapping_method": row.get("판정방법", ""),
                        "mapping_note": descriptions[mid].get("검토사유", "")}
                if not compound and len(components) == 1 and (method := cooking_methods.get(mid)):
                    menu["cooking_method"] = method
                    counts["reference_cooking_method"] += 1
                db.execute("INSERT INTO catalog VALUES(?,?)", (mid, json.dumps(menu, ensure_ascii=False)))
            counts["catalog"] = db.execute("SELECT count(*) FROM catalog").fetchone()[0]
            if not counts["catalog"] or not counts["scored_nutrition"]:
                raise ValueError(f"카탈로그의 필수 연결이 비어 있습니다: {dict(counts)}")
            checks = validate_links(src, db, stores)
            db.commit()
            assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
        os.replace(temporary, CATALOG)
        report = {"counts": dict(counts), "aggregate_differences_by_source_category": aggregate_differences,
                  "linked_quality": checks}
        (LOCAL / "build-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
        print(dict(counts), flush=True)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true", help="직전에 전수 검사한 DB로 재생성")
    args = parser.parse_args()
    if not args.build_only:
        scan()
        scan_drive()
    build()
