"""추천 기준표 v1.3에 따른 음식(컬럼) 추천과 컬럼 안 가게 순위.

데이터는 PostgreSQL `pickmenu_data`의 current_ 뷰에서 읽는다. 계산 함수는 DB 없이
딕셔너리 목록만으로 동작해 테스트할 수 있다. 규칙과 문구는 docs/menu-recommend.md에 있다.
"""

import math
import os
import statistics
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

DISTRICTS = ("권선구", "영통구", "장안구", "팔달구")
GOALS = {"balance": "balance_score", "protein": "protein_score"}
GOAL_BASE = {"balance": 37.8, "protein": 18.6}
RATING_LOW, RATING_HIGH = 4.38, 4.83
SPICY = {"매움", "약간 매움", "보통 매움(가게마다 다름)"}
MILD_BONUS = 0.6
LIMIT = 5
WEIGHTS = (0.5, 0.3, 0.2)
CITY = "수원시 전체"


@dataclass(frozen=True)
class Query:
    district: str = ""
    goal: str = "balance"
    diet: str = "general"
    groups: tuple[str, ...] = ()
    mild: bool = False
    broth: str = ""

    @classmethod
    def parse(cls, payload: Any, visible_groups: set[str], all_groups: set[str]) -> "Query":
        if not isinstance(payload, dict):
            raise ValueError("추천 조건을 확인해 주세요.")
        district = payload.get("district") or ""
        goal = payload.get("goal") or "balance"
        diet = payload.get("diet") or "general"
        spice = payload.get("spice") or ""
        broth = payload.get("broth") or payload.get("soup") or ""
        groups = payload.get("groups") or []
        if any(not isinstance(value, str) for value in (district, goal, diet, spice, broth)):
            raise ValueError("추천 조건은 목록에서 선택해 주세요.")
        if district not in ("", *DISTRICTS):
            raise ValueError("지역은 수원시의 구 중에서 골라 주세요.")
        if goal not in GOALS:
            raise ValueError("영양 목표는 균형 식사 또는 단백질 중심만 고를 수 있어요.")
        if diet in {"vegetarian", "vegan"}:
            raise ValueError("채식 여부를 확인할 자료가 없어 지금은 추천하지 않아요.")
        if diet not in {"general", "meat"}:
            raise ValueError("식단 유형을 확인해 주세요.")
        if spice not in {"", "mild", "hot"} or broth not in {"", "yes", "no"}:
            raise ValueError("맵기와 국물 조건을 확인해 주세요.")
        if isinstance(groups, str):
            groups = [groups] if groups else []
        if not isinstance(groups, list) or len(groups) > 5 or not all(isinstance(g, str) for g in groups):
            raise ValueError("선호 음식군은 5개까지 고를 수 있어요.")
        unknown = [g for g in groups if g not in all_groups]
        if unknown:
            raise ValueError(f"없는 음식군이에요: {', '.join(unknown)}")
        del visible_groups  # 숨김 음식군은 받아도 후보가 없어 '선호 밖'으로만 채운다.
        return cls(district, goal, diet, tuple(dict.fromkeys(groups)), spice == "mild", broth)

    def filters_label(self) -> str:
        parts = (["안 매운"] if self.mild else []) + (
            ["국물 있음"] if self.broth == "yes" else ["국물 없음"] if self.broth == "no" else [])
        return " · ".join(parts)


@dataclass
class Data:
    menus: list[dict[str, Any]]
    columns: dict[str, dict[str, Any]]
    group_columns: dict[str, set[str]]
    visible_groups: list[str]
    batch_id: str = ""

    @property
    def meal_columns(self) -> set[str]:
        """① 추천 대상: 식사이고 곁들임이 아닌 컬럼."""
        return {name for name, c in self.columns.items() if c["is_meal"] and not c["is_side"]}

    def groups_of(self, column: str) -> list[str]:
        return [g.strip() for g in (self.columns[column].get("groups") or "").split(";") if g.strip()]

    def first_group(self, column: str) -> str:
        groups = self.groups_of(column)
        return groups[0] if groups else ""

    def preferred(self, groups: list[str]) -> set[str]:
        """컬럼표의 음식군 소속으로 선호 컬럼을 정한다(① 후보 대응표보다 넓다)."""
        chosen = set(groups)
        return {name for name in self.columns if chosen & set(self.groups_of(name))}


def keep(menu: dict[str, Any], query: Query, district: str) -> bool:
    if district and menu["district"] != district:
        return False
    if query.mild and menu["spiciness"] in SPICY:
        return False
    if query.broth == "yes" and menu["broth"] != "있음":
        return False
    if query.broth == "no" and menu["broth"] == "있음":
        return False
    return True


@dataclass
class ColumnStat:
    name: str
    menus: list[dict[str, Any]]
    stores: int
    reviews: int

    @property
    def grade(self) -> str:
        if self.stores >= 3:
            return "full" if self.reviews >= 30 else "weak"
        if self.stores == 2 and self.reviews >= 30:
            return "weak"
        return "none"


def column_stats(data: Data, query: Query, district: str, filtered: bool = True) -> dict[str, ColumnStat]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for menu in data.menus:
        if filtered and not keep(menu, query, district):
            continue
        if not filtered and district and menu["district"] != district:
            continue
        grouped[menu["column_name"]].append(menu)
    return {name: ColumnStat(name, menus, len({m["integrated_store_id"] for m in menus}),
                             sum(m["review_count"] for m in menus))
            for name, menus in grouped.items()}


def percentile(values: list[float], value: float) -> float:
    less = sum(v < value for v in values)
    equal = sum(v == value for v in values)
    return (less + (equal + 1) / 2) / len(values) if values else 0.0


def weighted(menus: list[dict[str, Any]], key: str) -> tuple[float | None, float]:
    total = sum(m["review_count"] for m in menus)
    known = [m for m in menus if m.get(key) is not None]
    covered = sum(m["review_count"] for m in known)
    if not known or not covered:
        return None, 0.0
    value = sum(float(m[key]) * m["review_count"] for m in known) / covered
    return value, covered / total if total else 0.0


def score_columns(data: Data, query: Query, stats: dict[str, ColumnStat]) -> dict[str, dict[str, Any]]:
    meal = data.meal_columns
    reference = [math.log1p(s.reviews) for name, s in stats.items() if name in meal and s.stores >= 2]
    rows: dict[str, dict[str, Any]] = {}
    for name in sorted(meal):
        stat = stats.get(name)
        if stat is None or stat.grade == "none":
            continue
        nutrition, coverage = weighted(stat.menus, GOALS[query.goal])
        rating, _ = weighted(stat.menus, "rating")
        rows[name] = {"nutrition": nutrition if coverage >= 0.5 else None, "rating": rating or 0.0,
                      "popularity": percentile(reference, math.log1p(stat.reviews)), "stat": stat}
    known = [r["nutrition"] for r in rows.values() if r["nutrition"] is not None]
    fill = statistics.median(known) if known else GOAL_BASE[query.goal]
    for row in rows.values():
        if row["nutrition"] is None:
            row["nutrition"], row["nutrition_missing"] = fill, True
        level = min(1.0, max(0.0, (row["rating"] - RATING_LOW) / (RATING_HIGH - RATING_LOW)))
        row["score"] = (WEIGHTS[0] * row["nutrition"] / 100 + WEIGHTS[1] * row["popularity"]
                        + WEIGHTS[2] * level)
    return rows


def store_ranking(menus: list[dict[str, Any]], query: Query) -> list[dict[str, Any]]:
    """가게마다 리뷰가 가장 많은 메뉴 1개를 대표로 골라 추천점수 순으로 돌려준다."""
    best: dict[str, dict[str, Any]] = {}
    for menu in menus:
        current = best.get(menu["integrated_store_id"])
        key = (menu["review_count"], float(menu["recommendation_score"] or 0))
        if current is None or key > (current["review_count"], float(current["recommendation_score"] or 0)):
            best[menu["integrated_store_id"]] = menu

    def order(menu: dict[str, Any]) -> tuple[float, int, str]:
        bonus = MILD_BONUS if query.mild and menu["spiciness"] == "안 매움" else 0.0
        return (-(float(menu["recommendation_score"] or 0) + bonus), -menu["review_count"],
                menu["integrated_store_id"])

    return sorted(best.values(), key=order)


def public_store(menu: dict[str, Any], query: Query) -> dict[str, Any]:
    notes = []
    if query.mild and menu["spiciness"] == "일부 매운 옵션":
        notes.append("순한 메뉴를 골라 주문하세요")
    return {"store_id": menu["integrated_store_id"], "store_name": menu["store_name"],
            "district": menu["district"], "menu_name": menu["display_name"] or menu["detail_food"],
            "rating_display": float(menu["rating_display"]) if menu["rating_display"] is not None else None,
            "review_count": menu["review_count"], "few_reviews": bool(menu["few_reviews"]),
            "disappointing_reviews": menu["disappointing_reviews"],
            "store_repeat_ratio": float(menu["store_repeat_ratio"]) if menu["store_repeat_ratio"] is not None else None,
            "spiciness": menu["spiciness"], "broth": menu["broth"],
            "rice_included": bool(menu["rice_included"]),
            "portion_g": float(menu["portion_g"]) if menu["portion_g"] is not None else None,
            "notes": notes}


def recommend(data: Data, query: Query, store_limit: int = 10) -> dict[str, Any]:
    region = query.district or CITY
    stats = column_stats(data, query, query.district)
    rows = score_columns(data, query, stats)
    selected = [g for g in query.groups if g in data.visible_groups]
    preferred = data.preferred(selected)
    base = GOAL_BASE[query.goal]

    def unfit(name: str) -> bool:
        return rows[name]["nutrition"] < base

    def within(name: str) -> tuple[int, int, float, int]:
        stat = rows[name]["stat"]
        no_mild = query.mild and not any(m["spiciness"] == "안 매움" for m in stat.menus)
        meat = query.diet == "meat" and bool(data.columns[name]["meat_preferred"])
        return (int(no_mild), -int(meat), -rows[name]["score"], -stat.reviews)

    if query.groups:
        stages = [
            [n for n in rows if n in preferred and rows[n]["stat"].grade == "full"],
            [n for n in rows if n in preferred and rows[n]["stat"].grade == "weak"],
            [n for n in rows if n not in preferred and not unfit(n)],
            [n for n in rows if n not in preferred and unfit(n)],
        ]
    else:
        stages = [[n for n in rows if not unfit(n)], [n for n in rows if unfit(n)]]
    order: list[str] = []
    seen_groups: set[str] = set()
    for stage in stages:
        for name in sorted(stage, key=within):
            if len(order) >= LIMIT:
                break
            if not query.groups:
                first = data.first_group(name)
                if first in seen_groups:
                    continue
                seen_groups.add(first)
            order.append(name)

    items = []
    for name in order:
        row, stat = rows[name], rows[name]["stat"]
        notes = []
        if stat.grade == "weak":
            notes.append(f"{region} 가게 2곳" if stat.stores == 2 else f"{region} 리뷰 {stat.reviews}건")
        if query.groups and name not in preferred:
            notes.append("선호 밖")
        if unfit(name):
            notes.append("목표 점수 낮음")
        column = data.columns[name]
        items.append({
            "column": name, "display_name": column["display_name"] or name,
            "groups": data.groups_of(name),
            "grade": stat.grade, "store_count": stat.stores, "review_count": stat.reviews,
            "nutrition_score": round(row["nutrition"], 1),
            "nutrition_estimated": bool(row.get("nutrition_missing")),
            "rating_level": round(row["rating"], 3), "column_score": round(row["score"], 4),
            "preferred": name in preferred, "goal_low": unfit(name), "notes": notes,
            "stores": [public_store(m, query) for m in store_ranking(stat.menus, query)[:store_limit]],
        })
    return {"query": query.__dict__ | {"groups": list(query.groups)}, "region": region,
            "items": items, **expansion(data, query, rows, items, preferred, selected)}


def expansion(data: Data, query: Query, rows: dict[str, dict[str, Any]], items: list[dict[str, Any]],
              preferred: set[str], selected: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"expand": False, "expand_reason": "", "expand_hints": [],
                              "unavailable": []}
    hidden_only = bool(query.groups) and not selected
    if hidden_only:
        return result
    city = column_stats(data, query, "")
    # 넓히기 안내와 '조건으로 없음'은 대응표의 ① 후보 컬럼만 대상으로 한다.
    hint_columns = set().union(*(data.group_columns.get(g, set()) for g in selected)) if selected else set()
    if hint_columns and query.district:
        local = column_stats(data, query, query.district)
        unfiltered = column_stats(data, query, query.district, filtered=False)
        label = query.filters_label()
        for name in sorted(hint_columns):
            local_stat = local.get(name)
            if local_stat is not None and local_stat.grade != "none":
                continue
            city_stat = city.get(name)
            if city_stat is None or city_stat.grade == "none":
                result["unavailable"].append(name)
                continue
            before = unfiltered.get(name)
            by_filter = bool(label) and before is not None and before.grade != "none"
            result["expand_hints"].append({
                "column": name, "reason": "filter" if by_filter else "region",
                "district_stores": local_stat.stores if local_stat else 0,
                "city_stores": city_stat.stores,
                "message": (f"{query.district}에 {label} 가게 " if by_filter else f"{query.district} ")
                + f"{local_stat.stores if local_stat else 0}곳 → 수원 전체 {city_stat.stores}곳",
            })
    if not query.district:
        return result
    weak = sum(item["grade"] == "weak" for item in items)
    shown_preferred = sum(item["preferred"] for item in items)
    reason = ""
    if len(items) < LIMIT:
        reason = f"추천 {len(items)}개뿐"
        wider = recommend(data, Query("", query.goal, query.diet, query.groups, query.mild, query.broth),
                          store_limit=0)
        more = len(wider["items"]) > len(items) or bool(result["expand_hints"])
    elif preferred and shown_preferred <= 1:
        reason, more = "고른 음식 가게 적음", bool(result["expand_hints"])
    elif preferred and weak >= 3:
        reason, more = f"'약함' {weak}개", bool(result["expand_hints"])
    else:
        more = False
    if reason and more:
        result["expand"], result["expand_reason"] = True, reason
    return result


def stores_for_foods(data: Data, foods: list[str], district: str = "", limit: int = 10) -> dict[str, Any]:
    """기존 카탈로그 메뉴의 구성 음식 이름으로 가게를 찾는다.

    두 자료 사이에 검증된 ID 변환표가 없어서 세부음식(없으면 상위음식) 이름이 정확히 같을 때만
    연결한다. 비슷한 이름을 추측해 붙이지 않는다. 가게 순서는 ② 컬럼 안 가게 순위 규칙을 따른다.
    """
    wanted = {f.strip() for f in foods if f.strip()}
    matched = [m for m in data.menus if m["detail_food"] in wanted]
    if not matched:
        matched = [m for m in data.menus if m.get("upper_food") in wanted]
    local = [m for m in matched if not district or m["district"] == district]
    citywide = bool(district) and not local and bool(matched)
    ranked = store_ranking(matched if citywide else local, Query(district=district))
    return {"foods": sorted(wanted), "district": district, "citywide": citywide,
            "store_count": len(ranked), "stores": [public_store(m, Query()) for m in ranked[:limit]]}


TASTE_SHARE = 0.6
_tastes: dict[str, Any] = {"data": None, "value": None}


def _taste_label(counts: dict[str, float], first: str, second: str) -> str:
    total = sum(counts.values())
    for label in (first, second):
        if total and counts.get(label, 0) / total >= TASTE_SHARE:
            return label
    return ""


def food_tastes(data: Data) -> dict[str, tuple[str, str]]:
    """음식 이름(세부음식, 없으면 상위음식)별 맵기(hot·mild)와 국물(yes·no).

    가게 메뉴의 리뷰 수 가중 60% 이상이 같은 쪽일 때만 정하고, 섞여 있으면 빈 문자열로 둔다.
    '일부 매운 옵션'·'애매'는 어느 쪽으로도 세지 않고 분모에만 넣는다.
    """
    with _lock:
        if _tastes["data"] is data:
            return _tastes["value"]
    by_name: dict[str, dict[str, dict[str, float]]] = {}
    for level in ("detail_food", "upper_food"):
        counts: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: {"spice": {}, "soup": {}})
        for menu in data.menus:
            name = menu.get(level)
            if not name or name in by_name or menu["spiciness"] == "명시 없음":
                continue
            weight = float(max(menu["review_count"] or 0, 1))
            spice = "hot" if menu["spiciness"] in SPICY else "mild" if menu["spiciness"] == "안 매움" else "mixed"
            soup = {"있음": "yes", "없음": "no"}.get(menu["broth"], "mixed")
            for kind, value in (("spice", spice), ("soup", soup)):
                counts[name][kind][value] = counts[name][kind].get(value, 0.0) + weight
        by_name.update(counts)
    tastes = {name: (_taste_label(c["spice"], "hot", "mild"), _taste_label(c["soup"], "yes", "no"))
              for name, c in by_name.items()}
    with _lock:
        _tastes.update(data=data, value=tastes)
    return tastes


def public_groups(data: Data) -> list[dict[str, Any]]:
    return [{"name": g, "columns": sorted(data.group_columns.get(g, set()))} for g in data.visible_groups]


# ---------------------------------------------------------------- DB 연결

_cache: dict[str, Any] = {"data": None, "checked": 0.0}
_lock = threading.Lock()
CACHE_SECONDS = 300


def dsn() -> str:
    return os.environ.get("PICKMENU_REFERENCE_DSN", "service=pickmenu")


def load_from_db(conninfo: str | None = None) -> Data:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(conninfo or dsn(), row_factory=dict_row, connect_timeout=5) as db:
        db.read_only = True
        batch = db.execute("SELECT batch_id FROM pickmenu_data.active_dataset LIMIT 1").fetchone()
        menus = db.execute("""
            SELECT integrated_store_id, store_name, district, column_name, upper_food, detail_food, display_name,
                   rating_display, rating, review_count, few_reviews, disappointing_reviews,
                   store_repeat_ratio, recommendation_score, spiciness, broth, balance_score,
                   protein_score, rice_included, portion_g
            FROM pickmenu_data.current_menu_scores""").fetchall()
        columns = db.execute("""
            SELECT column_name, display_name, is_meal, is_side, eligible, meat_preferred,
                   data->>'선호음식군' AS groups
            FROM pickmenu_data.current_food_columns""").fetchall()
        groups = db.execute("""
            SELECT group_name, visible FROM pickmenu_data.current_preference_groups
            ORDER BY row_number""").fetchall()
        pairs = db.execute("SELECT group_name, column_name FROM pickmenu_data.current_preference_candidates").fetchall()
    for menu in menus:
        for key in ("rating", "balance_score", "protein_score", "recommendation_score"):
            if menu[key] is not None:
                menu[key] = float(menu[key])
    group_columns: dict[str, set[str]] = defaultdict(set)
    for pair in pairs:
        group_columns[pair["group_name"]].add(pair["column_name"])
    return Data(menus, {c["column_name"]: c for c in columns}, dict(group_columns),
                [g["group_name"] for g in groups if g["visible"]],
                next(iter(batch.values())) if batch else "")


def data() -> Data:
    """활성 배치를 5분 동안 메모리에 두고 재사용한다."""
    import time

    with _lock:
        cached = _cache["data"]
        if cached is None or time.monotonic() - _cache["checked"] > CACHE_SECONDS:
            _cache["data"] = load_from_db()
            _cache["checked"] = time.monotonic()
        return _cache["data"]
