"""대표 메뉴에 속한 실제 메뉴와 판매 매장을 찾는다.

카탈로그 SQLite의 `trace`(메뉴·리뷰·매장 ID)로 매장별 리뷰 수를 세고, 매장 이름은 참고자료
PostgreSQL의 매장 자료에서 같은 매장 ID(`SW_…`)로 붙인다. 매장 이름을 불러올 수 없으면 이름 없이
메뉴와 리뷰 수만 돌려준다.

원본 매장 ID는 배달앱마다 따로 있어 같은 가게가 여러 ID로 들어온다(배달앱 간 통합표 없음).
화면에는 같은 구에서 가게 이름이 같으면(띄어쓰기·기호 무시) 한 가게로 묶고 리뷰 수를 더한다.
원본 ID는 합치지 않으며, 이름이 다른 브랜드는 주소가 같아도 따로 둔다.
"""

import re
import sqlite3
import threading
import time
import unicodedata
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

import menu_reco

CACHE_SECONDS = 300
RETRY_SECONDS = 60
_lock = threading.Lock()
_names: dict[str, Any] = {"value": None, "checked": float("-inf")}
PLATFORMS = {"BAEMIN": "배민", "CPEATS": "쿠팡이츠", "YOGIYO": "요기요", "NAVER": "네이버"}


def name_key(name: str) -> str:
    """가게 이름 비교값. '제육대가 수원점'과 '제육대가-수원점'을 같게 본다."""
    return re.sub(r"[\s\-·_()\[\]{}<>.,/&+]", "", unicodedata.normalize("NFKC", name)).casefold()


def store_key(store_id: str, district: str, names: dict[str, str] | None) -> tuple[str, ...]:
    name = (names or {}).get(store_id)
    return ("name", name_key(name), district) if name and name_key(name) else ("id", store_id)


BRANCH = re.compile(r"^(.*\S)[\s\-]+[^\s\-]+점$")


def store_keys(locations: set[tuple[str, str]], names: dict[str, str] | None) -> dict[tuple[str, str], tuple[str, ...]]:
    """(매장 ID, 구)마다 묶음 키를 정한다.

    이름이 같으면(기호 무시) 같은 가게다. 지점명이 빠진 이름('불족대가')은 같은 구에 그 이름으로
    시작하는 지점('불족대가 수원점')이 하나뿐일 때만 그 지점에 합친다. 지점이 둘 이상이면 따로 둔다.
    """
    keys = {location: store_key(*location, names) for location in locations}
    branches: dict[tuple[str, str], set[tuple[str, ...]]] = {}
    for (store_id, district), key in keys.items():
        match = BRANCH.match((names or {}).get(store_id, ""))
        if match and name_key(match.group(1)):
            branches.setdefault((name_key(match.group(1)), district), set()).add(key)
    for location, key in keys.items():
        found = branches.get((key[1], location[1]), set()) if key[0] == "name" else set()
        if len(found) == 1:
            keys[location] = next(iter(found))
    return keys


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def _filter(menu_ids: list[str], district: str) -> tuple[str, list[str]]:
    where = f"trace.menu_id IN ({','.join('?' * len(menu_ids))})"
    params = list(menu_ids)
    if district:
        where += " AND review.district=?"
        params.append(district)
    return where, params


def evidence(path: Path, menu_ids: list[str], district: str) -> dict[str, int]:
    """대표 메뉴 전체의 중복 없는 리뷰 수와 가게 수(같은 이름 가게는 한 곳)."""
    where, params = _filter(menu_ids, district)
    with closing(connect(path)) as db:
        reviews, = db.execute(f"""
            SELECT count(DISTINCT review_id) FROM trace JOIN review USING(review_id) WHERE {where}""",
                              params).fetchone()
        locations = db.execute(f"""
            SELECT DISTINCT trace.store_id, review.district
            FROM trace JOIN review USING(review_id) WHERE {where}""", params).fetchall()
    return {"reviews": reviews, "stores": len(set(store_keys(set(locations), store_names()).values()))}


def store_rows(path: Path, menu_ids: list[str], district: str) -> list[tuple[str, str, str, str, int]]:
    """(메뉴 ID, 매장 ID, 구, 배달앱, 리뷰 수) 목록."""
    where, params = _filter(menu_ids, district)
    with closing(connect(path)) as db:
        return db.execute(f"""
            SELECT trace.menu_id, trace.store_id, review.district, review.platform, count(*)
            FROM trace JOIN review USING(review_id) WHERE {where}
            GROUP BY trace.menu_id, trace.store_id, review.district, review.platform""", params).fetchall()


def load_store_names() -> dict[str, str]:
    import psycopg

    queries = ("SELECT integrated_store_id, store_name FROM pickmenu_data.current_menu_scores",
               "SELECT integrated_store_id, store_name FROM pickmenu_data.current_store_id_review",
               "SELECT store_id, store_name FROM pickmenu_data.current_survey_stores")
    names: dict[str, str] = {}
    with psycopg.connect(menu_reco.dsn(), connect_timeout=5) as db:
        db.read_only = True
        for query in queries:
            for store_id, name in db.execute(query).fetchall():
                if store_id and name:
                    names.setdefault(store_id, name)
    return names


def store_names() -> dict[str, str] | None:
    """매장 이름을 5분 동안 메모리에 둔다. 불러오지 못하면 1분 뒤 다시 시도하고 None을 돌려준다."""
    with _lock:
        age = time.monotonic() - _names["checked"]
        if age < (CACHE_SECONDS if _names["value"] is not None else RETRY_SECONDS):
            return _names["value"]
        try:
            _names["value"] = load_store_names()
        except Exception:  # 드라이버 미설치·연결 실패는 이름 없이 진행한다.
            _names["value"] = None
        _names["checked"] = time.monotonic()
        return _names["value"]


def rating_index(reference: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """추천 기준표 v1.3의 (매장 ID, 음식) → 리뷰가 가장 많은 가게 메뉴 행."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in reference:
        for food in {row["detail_food"], row.get("upper_food")} - {None, ""}:
            current = index.get((row["integrated_store_id"], food))
            if current is None or row["review_count"] > current["review_count"]:
                index[(row["integrated_store_id"], food)] = row
    return index


def public_rating(row: dict[str, Any]) -> dict[str, Any]:
    def number(value: Any) -> float | None:
        return float(value) if value is not None else None

    return {"rating_display": number(row["rating_display"]), "review_count": row["review_count"],
            "disappointing_reviews": row["disappointing_reviews"], "few_reviews": bool(row["few_reviews"]),
            "store_repeat_ratio": number(row["store_repeat_ratio"])}


def stores(path: Path, menus: list[dict[str, Any]], district: str, limit: int = 50,
           ratings: dict[tuple[str, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """고른 순서의 실제 메뉴별 판매 가게. 고른 구에 없으면 수원 전체로 넓힌다.

    `ratings`(rating_index 결과)가 있으면 같은 매장 ID·같은 음식의 별점 표시값·아쉬운 리뷰·단골 주문
    비율을 붙인다. 묶인 가게는 기준표 리뷰가 가장 많은 원본 매장 값을 쓴다.
    """
    ids = [menu["standard_menu_id"] for menu in menus]
    rows = store_rows(path, ids, district)
    citywide = bool(district) and not rows
    if citywide:
        rows = store_rows(path, ids, "")
    names = store_names()
    keys = store_keys({(row[1], row[2]) for row in rows}, names)
    merged: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for mid, store_id, where, platform, count in rows:
        entry = merged.setdefault((mid, keys[(store_id, where)]), {
            "standard_menu_id": mid, "district": where, "reviews": 0,
            "by_store": Counter(), "by_platform": Counter()})
        entry["reviews"] += count
        entry["by_store"][store_id] += count
        entry["by_platform"][platform] += count
    order = {mid: at for at, mid in enumerate(ids)}
    by_id = {menu["standard_menu_id"]: menu for menu in menus}
    result = []
    for (mid, key), entry in merged.items():
        # 이름은 리뷰가 가장 많은 원본 가게의 표기를 쓴다.
        top = min(entry["by_store"], key=lambda sid: (-entry["by_store"][sid], sid))
        index = ratings or {}
        food = by_id[mid]["components"][0]
        found = [index[(sid, food)] for sid in entry["by_store"] if (sid, food) in index]
        best = max(found, key=lambda row: (row["review_count"], row["integrated_store_id"]), default=None)
        result.append({"rating": public_rating(best) if best else None,"standard_menu_id": mid, "menu_name": by_id[mid]["name"],
                       "store_name": (names or {}).get(top), "district": entry["district"],
                       "reviews": entry["reviews"], "store_ids": len(entry["by_store"]),
                       "platforms": [PLATFORMS.get(p, p) for p, _ in sorted(
                           entry["by_platform"].items(), key=lambda item: (-item[1], item[0]))],
                       "_order": (order[mid], -entry["reviews"], top)})
    result.sort(key=lambda item: item["_order"])
    for item in result:
        del item["_order"]
    return {"district": district, "citywide": citywide, "names_available": names is not None,
            "store_count": len({key for _, key in merged}), "total": len(result), "stores": result[:limit]}
