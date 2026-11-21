"""프랜차이즈 영양 엑셀과 카탈로그 연결 검토 자료를 PostgreSQL에 적재할 SQL로 만든다."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from import_reference import cell, literal

SHEETS = ("00_요약", "01_브랜드영양DB", "02_정규화메뉴_연결",
          "03_매장메뉴_연결", "04_전체매장_브랜드", "05_브랜드별_수집현황")


def read_workbook(path: Path) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        if set(book.sheetnames) != set(SHEETS):
            raise ValueError("프랜차이즈 영양정보 파일의 시트 구성을 확인해 주세요.")
        result = {}
        for name in SHEETS:
            rows = iter(book[name].values)
            headers = next(rows)
            if not headers or any(not isinstance(h, str) or not h for h in headers) or len(set(headers)) != len(headers):
                raise ValueError(f"시트의 열 이름이 유효하지 않아요: {name}")
            result[name] = [(i, dict(zip(headers, map(cell, row), strict=True)))
                            for i, row in enumerate(rows, 2) if any(value is not None for value in row)]
        return result
    finally:
        book.close()


def link_catalog(rows: dict[str, list[tuple[int, dict[str, Any]]]], catalog: Path
                 ) -> tuple[list[tuple[str, str, str, str]], dict[str, int]]:
    products = {(row["브랜드"], row["영양정보ID"]) for _, row in rows[SHEETS[1]]}
    candidates = {}
    counts: Counter[str] = Counter()
    for _, row in rows[SHEETS[2]]:
        if not row["후보수"]:
            continue
        counts["원본 후보"] += 1
        if row["후보수"] != 1:
            counts["복수 코드 후보"] += 1
            continue
        brand, key, code = row["브랜드"], row["원본메뉴고유키"], row["영양정보ID 후보"]
        if (brand, code) not in products:
            raise ValueError("브랜드·영양 코드가 원천 시트에 없어요.")
        if (brand, key) in candidates:
            raise ValueError("브랜드·원본 메뉴 키가 중복돼요.")
        candidates[(brand, key)] = code
        counts["단일 후보"] += 1
        counts["단일 승인"] += row["승인상태"] == "승인"
    with closing(sqlite3.connect(catalog.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        menus = dict(db.execute("SELECT original_key, menu_id FROM update_mapping WHERE menu_id IS NOT NULL"))
        observed = set(db.execute("SELECT DISTINCT store_id, menu_id FROM trace"))
    counts["카탈로그 단일 후보"] = sum(key in menus for _, key in candidates)
    matches = set()
    for _, row in rows[SHEETS[3]]:
        brand, key, store = row["브랜드후보"], row["원본메뉴고유키"], row["매장ID"]
        if (code := candidates.get((brand, key))) and (menu_id := menus.get(key)) and (store, menu_id) in observed:
            matches.add((brand, key, store, menu_id))
    counts["매장 연결 행"] = len(matches)
    counts["매장 ID"] = len({row[2] for row in matches})
    counts["매장·카탈로그 메뉴"] = len({row[2:] for row in matches})
    return sorted(matches), dict(counts)


DDL = """CREATE SCHEMA IF NOT EXISTS pickmenu_franchise;
SET LOCAL search_path TO pickmenu_franchise, pg_catalog;
CREATE TABLE IF NOT EXISTS workbooks (
    sha256 text PRIMARY KEY,
    file_name text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS source_rows (
    sha256 text NOT NULL REFERENCES workbooks,
    sheet text NOT NULL,
    row_number integer NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (sha256, sheet, row_number)
);
CREATE TABLE IF NOT EXISTS catalog_links (
    sha256 text NOT NULL REFERENCES workbooks,
    brand text NOT NULL,
    original_key text NOT NULL,
    store_id text NOT NULL,
    standard_menu_id text NOT NULL,
    PRIMARY KEY (sha256, brand, original_key, store_id, standard_menu_id)
);
CREATE OR REPLACE VIEW nutrition AS
    SELECT sha256, row_number, data->>'브랜드' AS brand, data->>'영양정보ID' AS nutrition_id,
           data->>'제품명' AS product_name, data->>'영양 기준량' AS basis,
           (data->>'열량(kcal)')::numeric AS energy_kcal, (data->>'단백질(g)')::numeric AS protein_g,
           (data->>'탄수화물(g)')::numeric AS carbohydrate_g, (data->>'지방(g)')::numeric AS fat_g,
           (data->>'당류(g)')::numeric AS sugar_g, (data->>'나트륨(mg)')::numeric AS sodium_mg,
           data->>'출처유형' AS source_type, data->>'출처 URL' AS source_url
    FROM source_rows WHERE sheet='01_브랜드영양DB';
CREATE OR REPLACE VIEW menu_candidates AS
    SELECT sha256, row_number, data->>'브랜드' AS brand, data->>'원본메뉴고유키' AS original_key,
           data->>'원본메뉴명' AS original_name, data->>'영양정보ID 후보' AS nutrition_id,
           (data->>'후보수')::integer AS candidate_count, data->>'승인상태' AS approval_status
    FROM source_rows WHERE sheet='02_정규화메뉴_연결' AND (data->>'후보수')::integer > 0;
CREATE OR REPLACE VIEW store_candidates AS
    SELECT l.sha256, l.store_id, l.standard_menu_id, c.brand, c.original_key, c.original_name,
           c.approval_status, n.nutrition_id, n.product_name, n.basis, n.energy_kcal, n.protein_g,
           n.carbohydrate_g, n.fat_g, n.sugar_g, n.sodium_mg, n.source_type, n.source_url
    FROM catalog_links l JOIN menu_candidates c USING (sha256, brand, original_key)
    JOIN nutrition n USING (sha256, brand, nutrition_id) WHERE c.candidate_count=1;
CREATE OR REPLACE VIEW approved_store_nutrition AS
    SELECT * FROM store_candidates WHERE approval_status='승인';
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("output", type=Path, help="Git에 넣지 않는 비공개 SQL 파일")
    args = parser.parse_args()
    rows = read_workbook(args.workbook)
    links, counts = link_catalog(rows, args.catalog)
    digest = literal(hashlib.sha256(args.workbook.read_bytes()).hexdigest())
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("BEGIN;\n" + DDL)
        handle.write(f"INSERT INTO workbooks(sha256,file_name) VALUES ({digest},{literal(args.workbook.name)}) "
                     "ON CONFLICT DO NOTHING;\n")
        for sheet, records in rows.items():
            for number, data in records:
                payload = literal(json.dumps(data, ensure_ascii=False, allow_nan=False))
                handle.write(f"INSERT INTO source_rows VALUES ({digest},{literal(sheet)},{number},{payload}::jsonb) "
                             "ON CONFLICT DO NOTHING;\n")
        handle.write(f"DELETE FROM catalog_links WHERE sha256={digest};\n")
        for link in links:
            fields = ",".join(map(literal, link))
            handle.write(f"INSERT INTO catalog_links VALUES ({digest},{fields});\n")
        handle.write("COMMIT;\n")
    print(json.dumps({"sha256": digest[1:-1], "sheets": {s: len(rs) for s, rs in rows.items()},
                      "catalog": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
