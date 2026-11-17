"""대표 메뉴에 속한 실제 메뉴와 판매 매장을 찾는다.

카탈로그 SQLite의 `trace`(메뉴·리뷰·매장 ID)로 매장별 리뷰 수를 세고, 매장 이름은 참고자료
PostgreSQL의 매장 자료에서 같은 매장 ID(`SW_…`)로 붙인다. 매장 이름을 불러올 수 없으면 이름 없이
메뉴와 리뷰 수만 돌려준다.
"""

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


def stores(path: Path, menus: list[dict[str, Any]], district: str, limit: int = 50) -> dict[str, Any]:
    """고른 순서의 실제 메뉴별 판매 매장. 고른 구에 없으면 수원 전체로 넓힌다."""
    ids = [menu["standard_menu_id"] for menu in menus]
    rows = store_rows(path, ids, district)
    citywide = bool(district) and not rows
    if citywide:
        rows = store_rows(path, ids, "")
    order = {mid: at for at, mid in enumerate(ids)}
    rows.sort(key=lambda row: (order[row[0]], -row[3], row[1]))
    names = store_names()
    by_id = {menu["standard_menu_id"]: menu for menu in menus}
    return {"district": district, "citywide": citywide, "names_available": names is not None,
            "store_count": len({row[1] for row in rows}), "total": len(rows),
            "stores": [{"standard_menu_id": mid, "menu_name": by_id[mid]["name"],
                        "store_name": (names or {}).get(store_id), "district": where, "reviews": count}
                       for mid, store_id, where, count in rows[:limit]]}
