"""추천 CSV와 지도 조사 XLSX를 PostgreSQL에 적재할 트랜잭션을 만든다."""

import argparse
import csv
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

SCHEMA = "pickmenu_data"
MAP_FILE = "픽메뉴_635매장_네이버API_재점검_통합.xlsx"
# 영문 열명:원본 열명:SQL 타입. 원본의 나머지 열도 data JSONB에 모두 보존한다.
SPECS = {
    "food_columns": ("컬럼표.csv", "", "column_name:컬럼:text;display_name:화면이름:text;"
        "is_meal:식사:boolean;is_side:곁들임(①제외):boolean;eligible:① 후보:boolean;"
        "store_count:가게수:integer;review_count:리뷰수:integer;"
        "balance_score:영양점수_균형:numeric;protein_score:영양점수_단백질:numeric;"
        "rating_level:별점수준:numeric;meat_preferred:육식:boolean"),
    "food_nutrition": ("음식_영양.csv", "", "upper_food:상위음식:text;detail_food:세부음식:text;"
        "energy_kcal:열량_kcal:numeric;protein_g:단백질_g:numeric;fat_g:지방_g:numeric;"
        "carbohydrate_g:탄수화물_g:numeric;sugars_g:당류_g:numeric;sodium_mg:나트륨_mg:numeric;"
        "basis:기준량:text;confidence:신뢰도:text;balance_score:점수_균형:numeric;"
        "protein_score:점수_단백질:numeric;rice_included:공깃밥포함:boolean;portion_g:1인분_g:numeric;"
        "portion_source:1인분출처:text;reference_food:대표DB식품명:text;note:비고:text"),
    "preference_groups": ("선호음식군_대응표.csv", "", "group_name:선호음식군:text;"
        "candidate_count:① 후보 컬럼수:integer;visible:선택지노출:boolean;ui_note:화면 처리:text"),
    "menu_scores": ("메뉴_추천.csv", "", "integrated_store_id:통합매장ID:text;store_name:통합가게명:text;"
        "district:구:text;column_name:컬럼:text;upper_food:상위음식:text;detail_food:세부음식:text;"
        "display_name:표시메뉴명:text;rating_display:별점_표시:numeric;rating:별점:numeric;"
        "review_count:리뷰수:integer;few_reviews:리뷰적음:boolean;positive:긍정:integer;"
        "mixed:혼합:integer;negative:부정:integer;neutral:중립:integer;"
        "disappointing_reviews:아쉬운리뷰수:integer;repeat_ratio:단골주문비율:numeric;"
        "store_repeat_ratio:가게단골주문비율:numeric;repeat_sample_count:재주문_표본수:integer;"
        "recommendation_score:추천점수:numeric;column_rank:컬럼내순위:integer;"
        "representative:가게대표:boolean;store_rank:컬럼내_가게순위:integer;"
        "spiciness:맵기:text;broth:국물:text;quantity_review:양_리뷰:text;"
        "balance_score:영양_균형:numeric;protein_score:영양_단백질:numeric;"
        "rice_included:공깃밥포함:boolean;portion_g:1인분_g:numeric"),
    "recommendation_examples": ("추천예시.csv", "", "profile:프로필:text;rank:순위:integer;"
        "column_name:컬럼:text;store_count:가게수:integer;review_count:리뷰수:integer;"
        "nutrition_score:영양점수:numeric;rating_level:별점수준:numeric;column_score:컬럼점수:numeric;"
        "expand_button:넓히기버튼:text;expand_reason:넓히기사유:text;expand_hint:넓히기안내:text;"
        "unavailable_with_filters:조건으로없음:text"),
    "survey_stores": (MAP_FILE, "01_매장635", "store_id:매장ID:text;store_name:매장명:text;"
        "review_count:정제리뷰수:integer;collected_address:기존 수집주소:text;"
        "online_address:온라인 확인주소:text;address_verification:주소 검증:text;"
        "posted_hours:게시 영업시간:text;hours_source:영업시간 출처:text;phone:전화번호:text;"
        "phone_source:전화번호 출처:text;administrative_status:행정상태:text;"
        "administrative_as_of:행정상태 기준일:text;administrative_source:행정상태 출처:text;"
        "closure_judgment:폐업 판단:text;operating_judgment:현재 운영 판단:text;"
        "last_review_at:최신 리뷰 작성일:text;verification_stage:정보 검증 단계:text;"
        "next_action:다음 확인 작업:text;longitude:경도(카카오):numeric;latitude:위도(카카오):numeric"),
    "store_evidence": (MAP_FILE, "02_근거기록", "store_id:매장ID:text;evidence_type:근거 분류:text;"
        "source_name:출처명:text;source_url:원문 URL:text;source_as_of:자료기준일:text;"
        "checked_at:이번 확인일:text;caution:근거 성격·주의:text"),
    "maps_api_checks": (MAP_FILE, "04_Maps_API점검", "store_id:매장ID:text;call_status:API 호출상태:text;"
        "result_status:API 결과상태:text;new_address:도로명주소(신규 API):text;longitude:경도:numeric;"
        "latitude:위도:numeric;address_match:주소일치도:text;error_code:오류코드:text;"
        "operating_status:실제 영업여부:text"),
}
KEYS = {
    "food_columns": ("column_name",), "food_nutrition": ("upper_food", "detail_food"),
    "preference_groups": ("group_name",),
    "menu_scores": ("integrated_store_id", "detail_food"),
    "recommendation_examples": ("profile", "rank"), "survey_stores": ("store_id",),
    "maps_api_checks": ("store_id",),
}


def literal(value: Any) -> str:
    text = str(value)
    if "\0" in text:
        raise ValueError("NUL 문자는 PostgreSQL text에 저장할 수 없습니다")
    return "'" + text.replace("'", "''") + "'"


def cell(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def read_sources(root: Path) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for name in [s[0] for s in SPECS.values()] + ["README.md", "추천기준표.md"]:
        if any(s["name"] == name for s in sources):
            continue
        folder = "07_지도데이터" if name == MAP_FILE else "06_메뉴별점추천"
        path = root / folder / name
        raw = path.read_bytes()
        source: dict[str, Any] = {"name": name, "sha256": hashlib.sha256(raw).hexdigest(),
                                  "bytes": len(raw), "tables": {}, "document": None}
        if name.endswith(".md"):
            source["document"] = raw.decode("utf-8-sig")
        elif name.endswith(".csv"):
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                    raise ValueError(f"중복/빈 헤더: {name}")
                if any(None in row or None in row.values() for row in rows):
                    raise ValueError(f"CSV 열 수 불일치: {name}")
                source["tables"][""] = rows
        else:
            book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
            try:
                for sheet in book:
                    values = [list(map(cell, row)) for row in sheet.values]
                    if sheet.title in {s[1] for s in SPECS.values() if s[0] == name}:
                        headers = values[0]
                        if len(set(headers)) != len(headers) or any(not h for h in headers):
                            raise ValueError(f"XLSX 헤더 오류: {sheet.title}")
                        source["tables"][sheet.title] = [dict(zip(headers, row))
                            for row in values[1:] if any(v is not None for v in row)]
                    else:
                        source["tables"][sheet.title] = [{"cells": row} for row in values]
            finally:
                book.close()
        if "\ufffd" in json.dumps(source, ensure_ascii=False):
            raise ValueError(f"인코딩 대체문자: {name}")
        sources.append(source)
    return sources


def generated(spec: str) -> str:
    name, original, kind = spec.split(":")
    value = f"NULLIF(data->>{literal(original)}, '')"
    if kind == "boolean":
        value = (f"CASE {value} WHEN '예' THEN 'true' WHEN '아니오' THEN 'false' "
                 f"ELSE {value} END")
    if kind == "integer":
        value = f"({value})::numeric"
    return f"{name} {kind} GENERATED ALWAYS AS (({value})::{kind}) STORED"


def validate_types(rows: list[dict[str, Any]], spec: str) -> None:
    for row in rows:
        for field in spec.split(";"):
            _, original, kind = field.split(":")
            if original not in row:
                raise ValueError(f"필수 열 누락: {original}")
            value = row[original]
            if value is None or value == "":
                continue
            if kind == "boolean" and str(value) not in {"True", "False", "예", "아니오"}:
                raise ValueError(f"불리언 형식 오류: {original}")
            if kind in {"integer", "numeric"}:
                try:
                    number = Decimal(str(value))
                except InvalidOperation as error:
                    raise ValueError(f"숫자 형식 오류: {original}") from error
                if not number.is_finite() or (kind == "integer" and number != number.to_integral()):
                    raise ValueError(f"유한수/정수 형식 오류: {original}")


def build_sql(sources: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    for file, sheet, spec in SPECS.values():
        validate_types(next(s for s in sources if s["name"] == file)["tables"][sheet], spec)
    batch = hashlib.sha256("".join(s["sha256"] for s in sources).encode()).hexdigest()
    statements = ["\\set ON_ERROR_STOP on", "BEGIN;", "SET standard_conforming_strings = on;",
        "SELECT pg_advisory_xact_lock(761117050);",
        f"CREATE SCHEMA IF NOT EXISTS {SCHEMA} AUTHORIZATION pickmenu;",
        f"SET LOCAL search_path = {SCHEMA}, public;", "SET LOCAL ROLE pickmenu;",
        "CREATE TABLE IF NOT EXISTS import_batches (batch_id text PRIMARY KEY, "
        "imported_at timestamptz NOT NULL DEFAULT now(), description text NOT NULL);",
        "CREATE TABLE IF NOT EXISTS active_dataset (singleton boolean PRIMARY KEY DEFAULT true "
        "CHECK(singleton), batch_id text NOT NULL REFERENCES import_batches);",
        "CREATE TABLE IF NOT EXISTS source_files (batch_id text REFERENCES import_batches, "
        "file_name text, sha256 text NOT NULL, bytes bigint NOT NULL, document text, "
        "PRIMARY KEY(batch_id,file_name));",
        "CREATE TABLE IF NOT EXISTS source_rows (batch_id text, file_name text, sheet_name text, "
        "row_number integer, data jsonb NOT NULL, PRIMARY KEY(batch_id,file_name,sheet_name,row_number), "
        "FOREIGN KEY(batch_id,file_name) REFERENCES source_files);",
        f"INSERT INTO import_batches(batch_id,description) VALUES ({literal(batch)},"
        "'추천 v1.3 및 635매장 조사 참고자료') ON CONFLICT DO NOTHING;", ""]
    for source in sources:
        statements.append("INSERT INTO source_files VALUES (" + ",".join([
            literal(batch), literal(source["name"]), literal(source["sha256"]), str(source["bytes"]),
            "NULL" if source["document"] is None else literal(source["document"])])
            + ") ON CONFLICT DO NOTHING;")
        for sheet, rows in source["tables"].items():
            structured = not sheet or sheet in {s[1] for s in SPECS.values()}
            for number, row in enumerate(rows, 2 if structured else 1):
                statements.append("INSERT INTO source_rows VALUES (" + ",".join([
                    literal(batch), literal(source["name"]), literal(sheet), str(number),
                    literal(json.dumps(row, ensure_ascii=False, allow_nan=False)) + "::jsonb"])
                    + ") ON CONFLICT DO NOTHING;")
    counts = {}
    for table, (file, sheet, spec) in SPECS.items():
        fields = ",\n".join(generated(s) for s in spec.split(";"))
        constraints = ""
        if table in KEYS:
            constraints += ", UNIQUE NULLS NOT DISTINCT(batch_id," + ",".join(KEYS[table]) + ")"
        if table == "menu_scores":
            constraints += (", CHECK(rating BETWEEN 0 AND 5), CHECK(rating_display BETWEEN 0 AND 4.9),"
                " CHECK(review_count = positive + mixed + negative),"
                " CHECK(disappointing_reviews = mixed + negative), CHECK(few_reviews = (review_count < 10)),"
                " CHECK(repeat_ratio BETWEEN 0 AND 1), CHECK(store_repeat_ratio BETWEEN 0 AND 1),"
                " CHECK(review_count >= 5), FOREIGN KEY(batch_id,column_name) "
                "REFERENCES food_columns(batch_id,column_name)")
        if table in {"food_nutrition", "menu_scores", "food_columns"}:
            constraints += (", CHECK(balance_score BETWEEN 0 AND 100),"
                            " CHECK(protein_score BETWEEN 0 AND 100)")
        if table in {"store_evidence", "maps_api_checks"}:
            constraints += ", FOREIGN KEY(batch_id,store_id) REFERENCES survey_stores(batch_id,store_id)"
        if table in {"survey_stores", "maps_api_checks"}:
            constraints += (", CHECK(longitude BETWEEN -180 AND 180), CHECK(latitude BETWEEN -90 AND 90),"
                            " CHECK((longitude IS NULL) = (latitude IS NULL))")
        statements += [f"CREATE TABLE IF NOT EXISTS {table} (batch_id text, file_name text, "
            "sheet_name text, row_number integer, data jsonb NOT NULL, " + fields
            + ", PRIMARY KEY(batch_id,row_number), FOREIGN KEY(batch_id,file_name,sheet_name,row_number) "
            "REFERENCES source_rows" + constraints + ");",
            f"INSERT INTO {table}(batch_id,file_name,sheet_name,row_number,data) SELECT "
            "batch_id,file_name,sheet_name,row_number,data FROM source_rows WHERE "
            f"batch_id={literal(batch)} AND file_name={literal(file)} AND sheet_name={literal(sheet)} "
            "ON CONFLICT DO NOTHING;",
            f"CREATE OR REPLACE VIEW current_{table} AS SELECT t.* FROM {table} t "
            "JOIN active_dataset a USING(batch_id);"]
        count = len(next(s for s in sources if s["name"] == file)["tables"][sheet])
        counts[table] = count
        statements.append(f"DO $$ BEGIN IF (SELECT count(*) FROM {table} WHERE batch_id="
            f"{literal(batch)}) <> {count} THEN RAISE EXCEPTION 'row count mismatch: {table}'; "
            "END IF; END $$;")
    statements += [
        "CREATE INDEX IF NOT EXISTS menu_scores_region_column ON menu_scores(batch_id,district,column_name);",
        "CREATE INDEX IF NOT EXISTS menu_scores_food ON menu_scores(batch_id,upper_food,detail_food);",
        # 대응표는 ① 후보만 담는다. 전체 컬럼의 음식군 소속은 별도 뷰로 보존한다.
        "CREATE OR REPLACE VIEW current_preference_candidates AS SELECT p.group_name,"
        "btrim(c.value) AS column_name FROM current_preference_groups p CROSS JOIN LATERAL "
        "regexp_split_to_table(p.data->>'컬럼', ',') c(value) WHERE btrim(c.value) <> '';",
        "CREATE OR REPLACE VIEW current_column_groups AS SELECT c.column_name, btrim(g.value) AS group_name "
        "FROM current_food_columns c CROSS JOIN LATERAL regexp_split_to_table(c.data->>'선호음식군', ';') "
        "g(value) WHERE btrim(g.value) <> '';",
        "CREATE OR REPLACE VIEW current_menu_details AS SELECT m.*, n.energy_kcal, n.protein_g, "
        "n.fat_g, n.carbohydrate_g, n.sugars_g, n.sodium_mg, n.basis AS nutrition_basis, "
        "n.confidence AS nutrition_confidence, (n.row_number IS NOT NULL) AS has_nutrition_reference "
        "FROM current_menu_scores m LEFT JOIN current_food_nutrition n ON "
        "m.upper_food=n.upper_food AND m.detail_food IS NOT DISTINCT FROM n.detail_food;",
        # 통합ID와 조사ID의 정확 일치만 노출. 별칭·좌표·영업상태를 통합 매장에 전파하지 않는다.
        "CREATE OR REPLACE VIEW current_store_id_review AS SELECT DISTINCT m.integrated_store_id, "
        "m.store_name, s.store_id AS exact_survey_id, s.store_name AS survey_name, "
        "CASE WHEN s.store_id IS NULL THEN '연결 미확인' ELSE '대표 ID 일치(전체 별칭 매핑 아님)' END "
        "AS link_status FROM current_menu_scores m LEFT JOIN current_survey_stores s "
        "ON s.store_id=m.integrated_store_id;",
        f"INSERT INTO active_dataset VALUES(true,{literal(batch)}) ON CONFLICT(singleton) "
        "DO UPDATE SET batch_id=excluded.batch_id;",
        "COMMENT ON TABLE menu_scores IS '가게×세부음식 추천 참고값. 표시 별점과 계산 별점은 구분한다.';",
        "COMMENT ON TABLE food_nutrition IS '공공DB 100g당 참고값. 매장 실측 아님. 점수는 공깃밥 포함 조건 별도.';",
        "COMMENT ON TABLE survey_stores IS '635개 원본 매장ID. 독립 점포 수나 현재 영업 확인을 뜻하지 않는다.';",
        "COMMENT ON TABLE maps_api_checks IS '원본 API 미조회·DNS 실패 기록. 좌표를 임의 생성하지 않는다.';",
        "COMMENT ON TABLE recommendation_examples IS '검증용 예시. 서비스 실사용자나 운영 추천 결과가 아니다.';",
        "COMMENT ON TABLE source_rows IS '원본 모든 열 및 설명 시트 보존. 업무 테이블 생성 열의 근거.';",
        "COMMIT;",
    ]
    return "\n".join(statements) + "\n", {"batch_id": batch, "counts": counts}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path, help="비공개 SQL 파일 경로. Git에 넣지 마세요")
    args = parser.parse_args()
    sql, report = build_sql(read_sources(args.source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(sql, encoding="utf-8")
    args.output.chmod(0o600)
    print(json.dumps(report, ensure_ascii=False, indent=2))
