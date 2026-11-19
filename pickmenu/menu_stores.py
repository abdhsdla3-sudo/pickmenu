"""대표 메뉴에 속한 실제 메뉴와 판매 매장을 찾는다.

카탈로그 SQLite의 `trace`(메뉴·리뷰·매장 ID)로 매장별 리뷰 수를 세고, 매장 이름은 참고자료
PostgreSQL의 매장 자료에서 같은 매장 ID(`SW_…`)로 붙인다. 매장 이름을 불러올 수 없으면 이름 없이
메뉴와 리뷰 수만 돌려준다.
"""

import csv
import math
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import menu_reco

CACHE_SECONDS = 300
RETRY_SECONDS = 60
_lock = threading.Lock()
_names: dict[str, Any] = {"value": None, "checked": float("-inf")}


# 가게 점수 = 100 × (리뷰 0.6 + 거리 0.4). 위치를 고르지 않으면 지금처럼 리뷰 수 순서다.
# 거리 점수는 1km 이내 1, 5km 이상 0, 그 사이는 직선으로 줄인다(배달 가능 거리를 생각한 값).
# 좌표가 없는 가게는 거리 점수 0.5(중간)로 두고 '거리 정보 없음'으로 보여 준다.
STORE_WEIGHTS = {"reviews": 0.6, "distance": 0.4}
NEAR_KM, FAR_KM = 1.0, 5.0
UNKNOWN_DISTANCE = 0.5
REVIEW_FULL = 200
_coords: dict[str, Any] = {"key": None, "value": {}, "address": {}}


def store_locations() -> dict[str, tuple[float, float]]:
    """PICKMENU_STORE_COORDS(geocode_stores.py 결과 CSV)의 매장ID → (위도, 경도). 파일이 바뀌면 다시 읽는다."""
    configured = os.environ.get("PICKMENU_STORE_COORDS")
    if not configured:
        return {}
    path = Path(configured).expanduser()
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return {}
    with _lock:
        if _coords["key"] == key:
            return _coords["value"]
    value: dict[str, tuple[float, float]] = {}
    address: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                lat, lng = float(row["latitude"]), float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if row.get("status", "").startswith("OK") and 33 <= lat <= 39 and 124 <= lng <= 132:
                value[row["store_id"]] = (lat, lng)
                address[row["store_id"]] = row.get("road_address") or row.get("address_used") or ""
    with _lock:
        _coords.update(key=key, value=value, address=address)
    return value


def store_address(store_id: str) -> str:
    """좌표 CSV의 도로명 주소(store_locations()를 먼저 부른 뒤 쓴다)."""
    return _coords["address"].get(store_id, "")


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """두 (위도, 경도) 사이 직선 거리(km, 하버사인)."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def store_score(reviews: int, km: float | None) -> float:
    review = min(1.0, math.log1p(reviews) / math.log1p(REVIEW_FULL))
    distance = UNKNOWN_DISTANCE if km is None else max(0.0, min(1.0, (FAR_KM - km) / (FAR_KM - NEAR_KM)))
    return 100 * (STORE_WEIGHTS["reviews"] * review + STORE_WEIGHTS["distance"] * distance)


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
    """대표 메뉴 전체의 중복 없는 리뷰 수와 매장 수."""
    where, params = _filter(menu_ids, district)
    with closing(connect(path)) as db:
        reviews, stores = db.execute(f"""
            SELECT count(DISTINCT review_id), count(DISTINCT trace.store_id)
            FROM trace JOIN review USING(review_id) WHERE {where}""", params).fetchone()
    return {"reviews": reviews, "stores": stores}


def store_rows(path: Path, menu_ids: list[str], district: str) -> list[tuple[str, str, str, int]]:
    """(메뉴 ID, 매장 ID, 구, 리뷰 수) 목록."""
    where, params = _filter(menu_ids, district)
    with closing(connect(path)) as db:
        return db.execute(f"""
            SELECT trace.menu_id, trace.store_id, review.district, count(*)
            FROM trace JOIN review USING(review_id) WHERE {where}
            GROUP BY trace.menu_id, trace.store_id, review.district""", params).fetchall()


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


def load_store_facts() -> dict[str, dict[str, Any]]:
    """가게(통합매장ID)별 추천이유 근거. 참고자료 매장 메뉴 행을 가게 단위로 모은다.

    - `positive_ratio`: 긍정 리뷰 / 리뷰 수(가게 전체 리뷰 10건 이상일 때)
    - `repeat_ratio`: 가게 단골 주문 비율(재주문 표본 20건 이상일 때)
    - `quantity`: 양 리뷰 판정(많음·보통·적음) 중 리뷰 수 가중 최다값. '언급없음'은 세지 않는다.
    """
    import psycopg

    totals: dict[str, dict[str, Any]] = {}
    with psycopg.connect(menu_reco.dsn(), connect_timeout=5) as db:
        db.read_only = True
        rows = db.execute("""
            SELECT integrated_store_id, review_count, positive, store_repeat_ratio, repeat_sample_count,
                   quantity_review FROM pickmenu_data.current_menu_scores""").fetchall()
    for store_id, reviews, positive, repeat, samples, quantity in rows:
        if not store_id:
            continue
        item = totals.setdefault(store_id, {"reviews": 0, "positive": 0, "repeat": None, "samples": 0,
                                            "quantity": {}})
        item["reviews"] += reviews or 0
        item["positive"] += positive or 0
        if repeat is not None and (samples or 0) >= item["samples"]:
            item["repeat"], item["samples"] = float(repeat), samples or 0
        if quantity in {"많음", "보통", "적음"}:
            item["quantity"][quantity] = item["quantity"].get(quantity, 0) + (reviews or 0)
    facts: dict[str, dict[str, Any]] = {}
    for store_id, item in totals.items():
        fact: dict[str, Any] = {}
        if item["reviews"] >= 10:
            fact["positive_ratio"] = round(item["positive"] / item["reviews"], 3)
        if item["repeat"] is not None and item["samples"] >= 20:
            fact["repeat_ratio"] = round(item["repeat"], 3)
        if item["quantity"]:
            fact["quantity"] = max(item["quantity"].items(), key=lambda pair: (pair[1], pair[0]))[0]
        facts[store_id] = fact
    return facts


_facts: dict[str, Any] = {"value": None, "checked": float("-inf")}


def store_facts() -> dict[str, dict[str, Any]] | None:
    """가게 근거를 5분 동안 메모리에 둔다. 불러오지 못하면 1분 뒤 다시 시도하고 None을 돌려준다."""
    with _lock:
        age = time.monotonic() - _facts["checked"]
        if age < (CACHE_SECONDS if _facts["value"] is not None else RETRY_SECONDS):
            return _facts["value"]
        try:
            _facts["value"] = load_store_facts()
        except Exception:  # 근거가 없으면 카탈로그 값(가게 수·리뷰 몫)만으로 문장을 쓴다.
            _facts["value"] = None
        _facts["checked"] = time.monotonic()
        return _facts["value"]


def stores(path: Path, menus: list[dict[str, Any]], district: str, limit: int = 50,
           origin: tuple[float, float] | None = None) -> dict[str, Any]:
    """고른 순서의 실제 메뉴별 판매 매장. 고른 구에 없으면 수원 전체로 넓힌다.

    `origin`(위도, 경도)이 있으면 메뉴 안에서 가게 점수(리뷰 0.6 + 거리 0.4) 순서로 정렬하고 거리를 붙인다.
    위치는 이 요청 계산에만 쓰고 저장하지 않는다.
    """
    ids = [menu["standard_menu_id"] for menu in menus]
    rows = store_rows(path, ids, district)
    citywide = bool(district) and not rows
    if citywide:
        rows = store_rows(path, ids, "")
    order = {mid: at for at, mid in enumerate(ids)}
    places = store_locations()
    store_ids = {row[1] for row in rows}
    km = {sid: distance_km(origin, places[sid]) for sid in store_ids & places.keys()} if origin else {}
    scores = {(row[0], row[1]): store_score(row[3], km.get(row[1])) for row in rows} if origin else {}
    rows.sort(key=lambda row: (order[row[0]], -scores.get((row[0], row[1]), 0), -row[3], row[1]))
    names = store_names()
    by_id = {menu["standard_menu_id"]: menu for menu in menus}
    result: dict[str, Any] = {
        "district": district, "citywide": citywide, "names_available": names is not None,
        "store_count": len(store_ids), "total": len(rows),
        "location_used": origin is not None, "located": len(km),
        "stores": [{"standard_menu_id": mid, "menu_name": by_id[mid]["name"],
                    "store_name": (names or {}).get(store_id), "district": where, "reviews": count}
                   for mid, store_id, where, count in rows[:limit]]}
    # 추천이유 문장의 근거: 같은 메뉴를 파는 가게 수와 그 메뉴 리뷰 중 이 가게 몫(잘라 내기 전 전체 기준).
    menu_reviews: dict[str, int] = {}
    menu_stores: dict[str, set[str]] = {}
    for mid, store_id, _, count in rows:
        menu_reviews[mid] = menu_reviews.get(mid, 0) + count
        menu_stores.setdefault(mid, set()).add(store_id)
    facts = store_facts() or {}
    for store, row in zip(result["stores"], rows[:limit], strict=True):
        mid, store_id, count = row[0], row[1], row[3]
        store["evidence"] = {"menu_stores": len(menu_stores[mid]),
                             "menu_share": round(count / menu_reviews[mid], 3) if menu_reviews[mid] else None,
                             **facts.get(store_id, {})}
        # 지도 표시용 가게 위치(도로명 주소를 바꾼 공개 좌표). 없으면 지도에서 빠진다.
        store["location"] = list(places[row[1]]) if row[1] in places else None
        store["address"] = store_address(row[1]) or None
        if origin:
            store["distance_km"] = round(km[row[1]], 1) if row[1] in km else None
            store["store_score"] = round(scores[(row[0], row[1])])
    return result
