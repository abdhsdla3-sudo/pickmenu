"""Drive 자료를 파일별로 읽고, 필요한 연결 키와 집계용 열을 저장한다."""

import csv
import hashlib
import io
import json
import sqlite3
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

from openpyxl import load_workbook

from raw import LOCAL, digest

DRIVE = LOCAL / "drive"
RECIPE = "09_식약처_레시피DB.xlsx"
EXPECTED = """
README.md
01_수원시_매장기준정보_2607-2609_설명.md
01_수원시_매장기준정보_2607-2609.xlsx
01_수원시_전체리뷰_설명.md
01_수원시_전체리뷰.csv
02_수원시_동일사업자동일주소_다른가게명_설명.md
02_수원시_동일사업자동일주소_다른가게명.csv
02_수원시_메뉴정규화_2607-2609_설명.md
02_수원시_메뉴정규화_2607-2609.xlsx
03_수원시_리뷰정제_2607-2609_설명.md
03_수원시_리뷰정제_2607-2609.xlsx
03_수원시_재주문데이터_2607-2609_설명.md
03_수원시_재주문데이터_2607-2609.csv
04_수원시_리뷰평가분류_2607-2609 (1).xlsx
04_수원시_리뷰평가분류_2607-2609_설명.md
04_수원시_리뷰평가분류_2607-2609.xlsx
05_수원시_매장메뉴집계_2607-2609_설명.md
05_수원시_매장메뉴집계_2607-2609.xlsx
06_공공영양정보_설명.md
06_공공영양정보_API원본_설명.md
06_공공영양정보_API원본.csv
06_공공영양정보.xlsx
07_메뉴영양정보_연결후보_설명.md
07_메뉴영양정보_연결후보.xlsx
08_데이터검증_수정내역_설명.md
08_데이터검증_수정내역.xlsx
09_식약처_레시피DB_설명.md
09_식약처_레시피DB.xlsx
공유전_점검결과.md
메뉴_음식종류_영양결합.csv
메뉴영양결합_설명.md
모델링_변수선정.md
식약처 K-FIND — 영양성분.zip
엑셀_전체변수목록.md
음식종류_영양연결표.csv
감성분류데이터/전체.csv
감성분류데이터/정답셋.csv
감성분류데이터/최종라벨_보기용.xlsx
감성분류데이터/최종라벨.csv
감성분류데이터/학습.csv
메뉴정규화_최종/메뉴_음식종류.csv
메뉴정규화_최종/메뉴정규화_결과_설명.md
메뉴정규화_최종/음식종류목록.csv
""".strip().splitlines()

NUTRIENTS = ("열량_kcal", "단백질_g", "탄수화물_g", "지방_g", "나트륨_mg")
OFFICIAL = ("에너지(kcal)", "단백질(g)", "탄수화물(g)", "지방(g)", "나트륨(mg)")
KEYS = set("""
리뷰ID review_id review_row_id 매장ID store_id 표준메뉴ID 메뉴ID 영양식품ID 식품코드
선택영양식품ID 참고레시피ID 레시피ID RCP_SEQ NUTR_NO
""".split())
PICKS = KEYS | set("""
source_file source_review_row district platform created_at_KST menus order_count order_count_present
시군구 플랫폼 표준메뉴명 원본메뉴명 메뉴순번 음식분류 항목유형 묶음메뉴표시여부 정규화처리 검토사유
관련리뷰수 주문횟수보유리뷰수 완전중복대표행여부 완전중복그룹ID 최종리뷰감성 전체감성
음식1 음식2 음식종류 구성 그룹 판정방법 리뷰수 매장수 식품명 기준량_원문
표준화기준량 표준화단위 출처명 자료파일 식품기원명 자료생성방법 선정사유 자료출처URL
영양성분함량기준량 1인(회)분량\u0020참고량 식품중량
연결승인여부 검수상태 매칭방법 매칭근거및주의 참고연결검토상태 참고영양기준상태
사람검수완료여부 메뉴전체영양값확정여부 베타포함여부 레시피명 1인분중량_원문
조리방법 레시피연결승인여부
영양기준검수상태 평가속성 비교결과 항목 건수 내용 설명 영양연결상태
""".split()) | set(OFFICIAL) | {"1인(회)분량 참고량"} | {
    prefix + field for prefix in ("", "음식1_", "음식2_") for field in
    (*("표준화" + n for n in NUTRIENTS), "식품명", "영양식품ID", "출처명", "기준량_원문",
     "표준화기준량", "표준화단위", "자료파일", "연결상태", "선정사유", "영양연결용정규화명")
}


def as_text(value: Any) -> str:
    return "" if value is None else str(value)


def inspect_table(db: sqlite3.Connection, file: str, sheet: str,
                  source: Iterable[Iterable[Any]]) -> dict[str, Any]:
    rows = iter(source)
    headers = [as_text(v).strip() for v in next(rows, ())]
    indices = [(i, h) for i, h in enumerate(headers) if h in PICKS]
    key_indices = [(i, h) for i, h in indices if h in KEYS]
    missing: Counter[str] = Counter()
    status: Counter[str] = Counter()
    types: Counter[str] = Counter()
    fingerprint = hashlib.sha256()
    batch: list[tuple[str, str, int, str]] = []
    is_official = "!" in file
    keep_detail = not is_official or "(음식)" in file
    for number, row in enumerate(rows, 2):
        cells = list(row)
        status["input"] += 1
        if not any(value is not None and value != "" for value in cells):
            status["blank"] += 1
            continue
        if len(cells) > len(headers) and any(as_text(v) for v in cells[len(headers):]):
            status["column_error"] += 1
            continue
        if len(cells) < len(headers):
            cells.extend([None] * (len(headers) - len(cells)))
        status["processed"] += 1
        normalized = list(map(as_text, cells))
        fingerprint.update(json.dumps(normalized, ensure_ascii=False).encode())
        fingerprint.update(b"\n")
        missing.update(header for header, value in zip(headers, normalized) if not value)
        types.update(f"{headers[i]}:{type(cells[i]).__name__}" for i, _ in key_indices if normalized[i])
        if keep_detail and indices:
            selected = {h: normalized[i] for i, h in indices if normalized[i]}
            batch.append((file, sheet, number, json.dumps(selected, ensure_ascii=False)))
            if len(batch) >= 3000:
                db.executemany("INSERT INTO records VALUES(?,?,?,?)", batch)
                batch.clear()
    db.executemany("INSERT INTO records VALUES(?,?,?,?)", batch)
    return {"sheet": sheet, "columns": headers, "counts": dict(status), "missing": dict(missing),
            "key_types": dict(types), "rows_sha256": fingerprint.hexdigest(),
            "artifact": "drive.sqlite3/records" if keep_detail and indices else "drive-audit.json"}


def scan_drive() -> None:
    absent = [name for name in EXPECTED if not (DRIVE / name).is_file()]
    if absent:
        raise ValueError("Drive 누락: " + ", ".join(absent))
    output = []
    with sqlite3.connect(LOCAL / "drive.sqlite3", timeout=120) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sources(file TEXT PRIMARY KEY, sha TEXT, audit TEXT);
            CREATE TABLE IF NOT EXISTS records(
                file TEXT, sheet TEXT, row_number INTEGER, data TEXT,
                PRIMARY KEY(file, sheet, row_number)
            ) WITHOUT ROWID;
        """)
        files = [p for p in DRIVE.rglob("*") if p.is_file()]
        for path in sorted(files, key=lambda p: (p.suffix != ".md", p.as_posix())):
            name = path.relative_to(DRIVE).as_posix()
            sha, _ = digest(path)
            db.execute("BEGIN IMMEDIATE")
            cached = db.execute("SELECT audit FROM sources WHERE file=? AND sha=?",
                                (name, sha)).fetchone()
            cached_audit = json.loads(cached[0]) if cached else {}
            if cached_audit and (name != RECIPE or cached_audit.get("cooking_columns")):
                output.append(cached_audit)
                db.commit()
                continue
            db.execute("DELETE FROM records WHERE file=? OR file GLOB ?", (name, name + "!*"))
            audit: dict[str, Any] = {"file": name, "bytes": path.stat().st_size, "sha256": sha,
                                     "tables": [], "encoding": "UTF-8"}
            if name == RECIPE:
                audit["cooking_columns"] = True
            if path.suffix == ".md":
                body = path.read_text(encoding="utf-8-sig")
                audit["document_lines"] = len(body.splitlines())
                audit["headings"] = [s.strip("# ") for s in body.splitlines() if s.startswith("#")]
            elif path.suffix == ".xlsx":
                audit["encoding"] = "XLSX XML"
                with path.open("rb") as source:
                    book = load_workbook(source, read_only=True, data_only=True, keep_links=False)
                    try:
                        for sheet in book:
                            audit["tables"].append(inspect_table(db, name, sheet.title, sheet.values))
                    finally:
                        book.close()
            elif path.suffix == ".csv":
                with path.open(encoding="utf-8-sig", newline="") as source:
                    audit["tables"].append(inspect_table(db, name, "", csv.reader(source, strict=True)))
            elif path.suffix == ".zip":
                with zipfile.ZipFile(path) as archive:
                    for member in archive.infolist():
                        if member.is_dir():
                            continue
                        if not member.filename.endswith(".csv"):
                            raise ValueError(f"지원되지 않는 ZIP 항목: {member.filename}")
                        with archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig",
                                                                          newline="") as source:
                            report = inspect_table(db, name + "!" + member.filename, "",
                                                   csv.reader(source, strict=True))
                        report.update(member=member.filename, bytes=member.file_size,
                                      crc32=f"{member.CRC:08x}")
                        audit["tables"].append(report)
            else:
                raise ValueError(f"지원되지 않는 파일: {name}")
            db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?)",
                       (name, sha, json.dumps(audit, ensure_ascii=False)))
            db.commit()
            output.append(audit)
            print(name, [t["counts"].get("processed", 0) for t in audit["tables"]], flush=True)
    (LOCAL / "drive-audit.json").write_text(json.dumps(output, ensure_ascii=False, indent=2),
                                           encoding="utf-8")


def records(db: sqlite3.Connection, name: str, sheet: str = "") -> Iterator[dict[str, str]]:
    for row, payload in db.execute("SELECT row_number,data FROM records WHERE file=? AND sheet=?",
                                   (name, sheet)):
        yield {**json.loads(payload), "_row": str(row)}


if __name__ == "__main__":
    scan_drive()
