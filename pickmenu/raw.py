"""원본 리뷰 전체 행을 검사하고 수원 메뉴 근거를 SQLite에 보관한다."""

import csv
import hashlib
import json
import re
import sqlite3
import zipfile
import zlib
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
LOCAL = BASE / "local"
RAW = BASE.parent / "순천향대_제공_리뷰_데이터"
DISTRICT = re.compile(r"(?:경기도?\s+)?수원시?\s+(권선구|영통구|장안구|팔달구)(?:\s|$)")
PERIODS = ("2509_2512", "2601_2604", "2605_2606", "2607_2608", "2609")
FIELDS = ("business_number", "platform_shop_name", "platform", "shop_address", "created_at",
          "reviewer", "rating", "content", "menus", "order_count", "order_type", "review_type",
          "visit_count", "keywords")


def key(*values: str) -> str:
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def digest(path: Path) -> tuple[str, int]:
    sha = hashlib.sha256()
    crc = 0
    with path.open("rb") as f:
        while block := f.read(1024 * 1024):
            sha.update(block)
            crc = zlib.crc32(block, crc)
    return sha.hexdigest(), crc


def ordered_menus(value: str) -> list[str]:
    items = json.loads(value)
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise ValueError("문자열 배열 필요")
    return sorted({item.strip() for item in items if item.strip()})


def scan() -> list[dict[str, Any]]:
    LOCAL.mkdir(exist_ok=True)
    paths = sorted(RAW.glob("*.csv"))
    if any(not any(p.stem.endswith("_" + period) for p in paths) for period in PERIODS):
        raise ValueError("기간별 원본 CSV 5개가 필요합니다.")
    archive = RAW.with_suffix(".zip")
    with zipfile.ZipFile(archive) as z:
        entries = {i.filename.encode("cp437").decode("utf-8", "replace"): i for i in z.infolist()}
    with sqlite3.connect(LOCAL / "raw.sqlite3", timeout=60) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS raw_file(name TEXT PRIMARY KEY, sha TEXT, audit TEXT);
            CREATE TABLE IF NOT EXISTS raw_review(
                file TEXT, row_number INTEGER, fingerprint TEXT, platform TEXT, month TEXT,
                district TEXT, branch TEXT, menus TEXT, order_count INTEGER, valid_row INTEGER,
                PRIMARY KEY(file,row_number)
            ) WITHOUT ROWID;
        """)
        if "valid_row" not in {row[1] for row in db.execute("PRAGMA table_info(raw_review)")}:
            db.execute("ALTER TABLE raw_review ADD COLUMN valid_row INTEGER")
            db.execute("DELETE FROM raw_file")
            db.commit()
        result = []
        for path in paths:
            sha, crc = digest(path)
            cached = db.execute("SELECT audit FROM raw_file WHERE name=? AND sha=?",
                                (path.name, sha)).fetchone()
            if cached:
                result.append(json.loads(cached[0]))
                print(path.name, "완료 파일 재사용", flush=True)
                continue
            same = [name for name, item in entries.items()
                    if item.file_size == path.stat().st_size and item.CRC == crc]
            counts: Counter[str] = Counter()
            distribution: Counter[str] = Counter()
            missing: Counter[str] = Counter()
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM raw_review WHERE file=?", (path.name,))
            valid_row = 0
            with path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f, strict=True)
                if tuple(next(reader)) != FIELDS:
                    raise ValueError(f"스키마 변경: {path.name}")
                for ordinal, row in enumerate(reader, 2):
                    counts["input"] += 1
                    if len(row) != len(FIELDS):
                        counts["business_only" if len(row) == 1 else "column_error"] += 1
                        continue
                    valid_row += 1
                    business, shop, platform, address, date, _, rating, _, menus, order = row[:10]
                    month = date[:7] if re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", date[:7]) else "미상"
                    district = DISTRICT.match(address.strip())
                    region = district[1] if district else ("주소 없음" if not address.strip() else "수원 외")
                    distribution[f"{month}|{platform or '미상'}|{region}"] += 1
                    missing.update(FIELDS[i] for i, v in enumerate(row) if not v)
                    try:
                        count = int(order) if order else None
                        if count is not None and count < 0:
                            raise ValueError
                    except ValueError:
                        counts["order_count_invalid"] += 1
                        count = None
                    try:
                        if rating not in ("", "-") and not 0 <= float(rating) <= 5:
                            raise ValueError
                    except ValueError:
                        counts["rating_invalid"] += 1
                    if rating == "-":
                        counts["rating_missing_marker"] += 1
                    try:
                        parsed = ordered_menus(menus) if menus.strip() else []
                    except ValueError:
                        counts["json_error"] += 1
                        if district:
                            counts["suwon_json_error"] += 1
                        parsed = []
                    if not parsed:
                        counts["menu_empty_or_invalid"] += 1
                    if not district:
                        counts["outside_or_unknown"] += 1
                        continue
                    counts["suwon"] += 1
                    if not parsed:
                        counts["suwon_menu_empty"] += 1
                    try:
                        kst = datetime.fromisoformat(date) + timedelta(hours=9)
                        if "2026-07" <= kst.strftime("%Y-%m") <= "2026-09":
                            counts["suwon_target_kst"] += 1
                    except ValueError:
                        counts["suwon_date_invalid"] += 1
                    # 리뷰어·본문은 해시의 입력으로만 사용한다.
                    branch = key(business, address.strip(), shop.strip(), platform)
                    db.execute("INSERT INTO raw_review VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (path.name, ordinal, key(*row), platform, month, region, branch,
                                json.dumps(parsed, ensure_ascii=False), count, valid_row))
            audit = {"file": path.name, "bytes": path.stat().st_size, "sha256": sha,
                     "crc32": f"{crc:08x}", "archive_matches": same, "encoding": "UTF-8",
                     "counts": dict(counts), "missing": dict(missing), "distribution": dict(distribution)}
            db.execute("INSERT OR REPLACE INTO raw_file VALUES(?,?,?)",
                       (path.name, sha, json.dumps(audit, ensure_ascii=False)))
            db.commit()
            result.append(audit)
            print(path.name, counts, flush=True)
        db.execute("CREATE INDEX IF NOT EXISTS raw_fingerprint ON raw_review(fingerprint)")
        db.execute("CREATE INDEX IF NOT EXISTS raw_valid_row ON raw_review(file,valid_row)")
    (LOCAL / "raw-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    scan()
