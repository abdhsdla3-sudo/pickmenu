"""추천 조건 검사, 점수 계산, 설명 생성을 담당한다."""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

WEIGHTS = {"nutrition": 0.50, "preference": 0.35, "evidence": 0.15}
GOALS = {"balance": "균형 식사", "protein": "단백질 중심", "light": "가벼운 식사"}
DISTRICTS = {"", "권선구", "영통구", "장안구", "팔달구"}
MEAT = re.compile(r"소고기|쇠고기|우삼겹|돼지|삼겹|닭|오리|족발|보쌈|스테이크|돈까스|돈가스|치킨|베이컨|소시지|곱창|막창|갈비|육회|불고기")
ANIMAL = re.compile(r"생선|새우|해물|해산물|오징어|아귀|아구|참치|연어|멸치|젓갈|굴소스|조개|바지락|전복|장어")
VEGAN_EXCLUDE = re.compile(r"달걀|계란|우유|치즈|버터|유청|요거트|꿀")
ALIASES = (("계란", "달걀", "난류", "에그"), ("우유", "유제품", "치즈", "버터", "유청"),
           ("밀", "밀가루", "밀글루텐"), ("땅콩", "피넛"), ("돼지", "돼지고기", "돈육"),
           ("새우", "쉬림프"), ("대두", "콩", "두부", "두유"))


def compact(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


@dataclass(frozen=True)
class Profile:
    diet: str
    goal: str
    spice: str
    soup: str
    category: str
    district: str
    query: str
    exclude: str
    allergy: str
    meal_category: str = ""

    @classmethod
    def parse(cls, data: Any, categories: list[str]) -> "Profile":
        if not isinstance(data, dict):
            raise ValueError("입력 형식을 확인해 주세요.")
        values = {}
        enums = {"diet": {"general", "vegetarian", "vegan", "meat"}, "goal": set(GOALS),
                 "spice": {"", "mild", "hot"}, "soup": {"", "yes", "no"},
                 "category": {"", *categories}, "meal_category": {"", *categories}, "district": DISTRICTS}
        for field, options in enums.items():
            value = data.get(field, {"diet": "general", "goal": "balance"}.get(field, ""))
            if not isinstance(value, str) or value not in options:
                raise ValueError("목록에 있는 조건을 선택해 주세요.")
            values[field] = value
        for field, maximum in {"query": 40, "exclude": 100, "allergy": 100}.items():
            value = data.get(field, "")
            if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
                raise ValueError(f"입력은 {maximum}자 이내로 작성해 주세요.")
            if field != "query" and value.strip() and not re.search(r"[^,;/\s]", value):
                raise ValueError("제한 재료 이름을 입력해 주세요.")
            values[field] = value.strip()
        return cls(**values)


def ingredient_terms(value: str) -> set[str]:
    terms = {compact(term) for term in re.split(r"[,;/]", value) if term.strip()}
    for group in ALIASES:
        if terms.intersection(group):
            terms.update(group)
    return terms


def clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def nutrition_score(nutrition: dict[str, Any] | None, goal: str) -> float | None:
    if not nutrition or nutrition["basis"] != 100 or nutrition["unit"] != "g":
        return None
    kcal, protein, carbs, fat, _ = nutrition["values"]
    if goal == "protein":
        return clip(protein / 12) if protein is not None else None
    if goal == "light":
        return clip(1 - (kcal - 80) / 220) if kcal is not None else None
    if kcal is None or kcal <= 0 or None in (protein, carbs, fat):
        return None
    total = 4 * protein + 4 * carbs + 9 * fat
    if total <= 0:
        return None
    distance = sum(abs(actual / total - target)
                   for actual, target in zip((4 * protein, 4 * carbs, 9 * fat), (.22, .53, .25)))
    return clip(1 - distance)


SOUP = {"있음": "yes", "없음": "no"}
SPICE_TABLE = {"보통 매움": "hot"}
MIN_SPICE_ROWS = 3
PREFERRED_FIRST = 3
# 이름 끝이 국물 요리인 음식(매운탕·어묵탕·만둣국·김치찌개·순대전골·뼈해장국 등)은 국물 있음으로 본다.
SOUP_NAME = re.compile(r"(탕|국|찌개|전골|국밥)$")


def column_spice(rows: list[dict[str, Any]]) -> dict[str, str]:
    """참고자료 매장 메뉴 행의 맵기(안 매움·약간 매움·매움·일부 매운 옵션)를 컬럼별로 모은다.

    판정이 있는 행이 3건 이상일 때만 정한다. `안 매움`이 80% 이상이면 mild, `매움`·`약간 매움`이
    절반 이상이면 hot, 섞여 있으면(일부 매운 옵션 포함) 정하지 않는다. `명시 없음`은 세지 않는다.
    """
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        level = row.get("spiciness") or ""
        if row.get("column_name") and level and level != "명시 없음":
            counts[row["column_name"]][level] += 1
    result: dict[str, str] = {}
    for column, count in counts.items():
        total = sum(count.values())
        if total < MIN_SPICE_ROWS:
            continue
        if count["안 매움"] >= 0.8 * total:
            result[column] = "mild"
        elif count["매움"] + count["약간 매움"] >= 0.5 * total:
            result[column] = "hot"
    return result


def reference_tags(columns: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """컬럼별 맵기·국물·선호음식군. 맵기는 매장 메뉴 판정을 먼저 쓰고 없으면 컬럼표 `보통 매움`을 쓴다."""
    spice = column_spice(rows)
    return {name: {"spice": spice.get(name) or SPICE_TABLE.get(column.get("spiciness") or "", ""),
                   "soup": SOUP.get(column.get("broth") or "", ""),
                   "groups": [group.strip() for group in (column.get("groups") or "").split(";") if group.strip()]}
            for name, column in columns.items()}


def match_column(food: str, names: list[str]) -> tuple[str | None, bool]:
    """표준 음식명에 맞는 컬럼과 정확히 맞았는지.

    정확히 같은 이름 → `·`로 묶인 컬럼의 한 이름(아구찜 → 아구찜·해물찜) → 끝이 같은 가장 긴
    컬럼 이름(두 글자 이상, 순대국밥 → 국밥, 로제떡볶이 → 떡볶이) 순으로 찾는다. 끝만 같은 경우는
    정확히 맞지 않은 것으로 돌려준다.
    """
    if food in names:
        return food, True
    for name in names:
        if food in name.split("·"):
            return name, True
    tails = sorted((len(part), name) for name in names for part in name.split("·")
                   if len(part) >= 2 and food.endswith(part))
    return (tails[-1][1], False) if tails else (None, False)


def apply_reference(menus: list[dict[str, Any]], tags: dict[str, dict[str, Any]] | None) -> list[dict[str, Any]]:
    """참고자료 맵기·국물·선호음식군을 표준 음식명(components[0])이 맞는 메뉴에 붙인다.

    카탈로그가 메뉴 이름으로 정한 맵기·국물은 그대로 두고 빈 값만 채운다. 이름 끝이 국물 요리면
    국물 있음으로 본다. 끝만 같은 컬럼(로제떡볶이 → 떡볶이)에서는 국물·음식군만 가져오고 맵기는
    가져오지 않는다. 음식군은 카탈로그 음식군 하나에 컬럼의 선호음식군(예: 짬뽕 → 중식·면)을 더한다.
    """
    tags = tags or {}
    names = sorted(tags)
    found: dict[str, tuple[str | None, bool]] = {}
    result: list[dict[str, Any]] = []
    for menu in menus:
        food = menu["components"][0]
        if food not in found:
            found[food] = match_column(food, names)
        name, exact = found[food]
        soup = menu["soup"] or ("yes" if SOUP_NAME.search(food) else "")
        if name is None:
            result.append({**menu, "soup": soup})
            continue
        tag = tags[name]
        result.append({**menu, "spice": menu["spice"] or (tag["spice"] if exact else ""),
                       "soup": soup or tag["soup"], "reference_column": name,
                       "groups": list(dict.fromkeys([menu["category"], *tag["groups"]]))})
    return food_tags(result, tags, found)


def food_tags(menus: list[dict[str, Any]], tags: dict[str, dict[str, Any]],
              found: dict[str, tuple[str | None, bool]]) -> list[dict[str, Any]]:
    """표준 음식(대표 메뉴) 자체의 맵기·국물을 `family_spice`·`family_soup`로 붙인다.

    이름이 정확히 맞는 참고자료 컬럼 값이 있으면 그것을 쓴다. 없으면 같은 음식 메뉴의 절반 넘게
    같은 값일 때만 그 값을 쓴다. `매콤한 감자피자` 하나 때문에 피자 전체가 매운 음식이 되지 않게 한다.
    """
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for menu in menus:
        members[menu["components"][0]].append(menu)
    decided: dict[str, dict[str, str]] = {}
    for food, group in members.items():
        name, exact = found.get(food, (None, False))
        values: dict[str, str] = {}
        for key in ("spice", "soup"):
            value = tags[name][key] if name and exact else ""
            if not value:
                top, count = Counter(menu[key] for menu in group if menu[key]).most_common(1)[0] \
                    if any(menu[key] for menu in group) else ("", 0)
                value = top if count * 2 > len(group) else ""
            values[key] = value
        decided[food] = values
    return [{**menu, "family_spice": decided[menu["components"][0]]["spice"],
             "family_soup": decided[menu["components"][0]]["soup"]} for menu in menus]


def recommend(menus: list[dict[str, Any]], profile: Profile) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    check: list[dict[str, Any]] = []
    excluded = unknown = mismatched = 0
    terms = ingredient_terms(profile.allergy) | ingredient_terms(profile.exclude)
    plant = profile.diet in {"vegan", "vegetarian"}
    region = profile.district or "수원시"
    for menu in menus:
        evidence = menu["regions"].get(region)
        groups = menu.get("groups") or [menu["category"]]
        signal = " ".join((menu["name"], menu["category"], *menu["components"]))
        if (not evidence or compact(profile.query) not in compact(signal)
                or (profile.meal_category and profile.meal_category not in groups)):
            continue
        # 고른 맵기·국물과 반대로 확인된 메뉴는 뺀다. 메뉴 이름의 단서뿐 아니라 음식 자체의 값도 본다
        # (예: 매운 쪽을 골랐을 때 '매콤한 감자피자'가 있어도 안 매운 음식인 피자는 뺀다).
        # 정보가 없는 메뉴는 남기고 점수만 낮춘다.
        if any(value and actual and value != actual
               for value, actual in ((profile.spice, menu["spice"]), (profile.soup, menu["soup"]),
                                     (profile.spice, menu.get("family_spice", "")),
                                     (profile.soup, menu.get("family_soup", "")))):
            mismatched += 1
            continue
        meat = bool(MEAT.search(signal))
        conflict = any(term in compact(signal) for term in terms) or (
            plant and (meat or ANIMAL.search(signal) or
                       (profile.diet == "vegan" and VEGAN_EXCLUDE.search(signal))))
        if conflict:
            excluded += 1
            continue
        if plant or terms:
            unknown += 1
            check.append({"standard_menu_id": menu["standard_menu_id"], "name": menu["name"],
                          "review_count": evidence["reviews"]})
            continue
        matches: list[str] = []
        preferences: list[float] = []
        category = profile.category if profile.category in groups else menu["category"]
        for label, value, actual in (("음식군", profile.category, category),
                                     # 맵기·국물 점수는 음식 자체의 값으로 매긴다. 매운오뎅김밥 하나로
                                     # 김밥이 매운 음식 1위가 되지 않게, 음식 값이 미정이면 정보 부족으로 본다.
                                     ("맵기", profile.spice, menu.get("family_spice", menu["spice"])),
                                     ("국물", profile.soup, menu.get("family_soup", menu["soup"])),
                                     ("육식 선호", "yes" if profile.diet == "meat" else "",
                                      "yes" if meat else "")):
            if value:
                preferences.append(1 if value == actual else (0.35 if not actual else 0))
                matches.append(f"{label} {'일치' if value == actual else '정보 부족' if not actual else '불일치'}")
        n = nutrition_score(menu["nutrition"], profile.goal)
        t = sum(preferences) / len(preferences) if preferences else 0.5
        e = (0.7 * clip(math.log1p(evidence["reviews"]) / math.log1p(150))
             + 0.3 * clip(math.log1p(evidence["stores"]) / math.log1p(10)))
        reasons = [f"{region} 리뷰 {evidence['reviews']:,}건 · 매장 {evidence['stores']:,}곳",
                   f"{GOALS[profile.goal]} 적합도 {round(n * 100)}점 (일반음식 참고값)"
                   if n is not None else "비교할 참고 영양값 부족", *matches]
        ranked.append({**menu, "region": region, "evidence": evidence, "reasons": reasons,
                       "_parts": (n, t, e), "_preferred": bool(profile.category) and profile.category in groups})
    # 영양 자료가 없는 메뉴는 감점하지 않고 이번 후보들의 영양 점수 중앙값을 쓴다.
    known = sorted(menu["_parts"][0] for menu in ranked if menu["_parts"][0] is not None)
    middle = (known[len(known) // 2] if len(known) % 2 else (known[len(known) // 2 - 1] + known[len(known) // 2]) / 2) \
        if known else 0.5
    for menu in ranked:
        n, t, e = menu.pop("_parts")
        score = WEIGHTS["nutrition"] * (n if n is not None else middle) + (
            WEIGHTS["preference"] * t + WEIGHTS["evidence"] * e)
        menu["score"] = round(score * 100, 3)
        menu["_unknown_nutrition"] = n is None
    # 선호 음식군에 맞는 음식이 3가지 이상이면 그 음식들을 먼저 보여 준다.
    preferred_first = len({compact(menu["components"][0]) for menu in ranked if menu["_preferred"]}) >= PREFERRED_FIRST
    # 점수가 같으면 영양 자료가 있는 메뉴를 앞에 둔다.
    ranked.sort(key=lambda x: (preferred_first and not x["_preferred"], -x["score"], x["_unknown_nutrition"],
                               -x["evidence"]["reviews"], x["standard_menu_id"]))
    for menu in ranked:
        del menu["_preferred"], menu["_unknown_nutrition"]
    check.sort(key=lambda x: (-x["review_count"], x["standard_menu_id"]))
    first_choices: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    seen: set[str] = set()
    for menu in ranked:
        family = compact(menu["components"][0])
        if family in seen:
            alternatives.append(menu)
        else:
            seen.add(family)
            first_choices.append(menu)
    return {"items": (first_choices + alternatives)[:5], "families": families(ranked),
            "eligible": len(ranked), "excluded": excluded, "mismatched": mismatched,
            "unverified": unknown, "check_only": check[:3], "region": region}


def families(ranked: list[dict[str, Any]], limit: int = 5, members: int = 30) -> list[dict[str, Any]]:
    """정렬된 추천 메뉴를 대표 메뉴(표준 음식명 components[0])별로 묶는다.

    가게마다 다른 메뉴 이름은 2차 보완 정규화의 표준 음식명이 같으면 하나의 대표 메뉴로 본다.
    대표 메뉴의 순서는 속한 메뉴 중 가장 높은 점수(정렬된 첫 메뉴)를 따른다.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for menu in ranked:
        groups.setdefault(menu["components"][0], []).append(menu)
    result = []
    for name, group in list(groups.items())[:limit]:
        best = group[0]
        result.append({"name": name, "category": best["category"], "menu_count": len(group),
                       "best": best, "members": [
                           {"standard_menu_id": m["standard_menu_id"], "name": m["name"],
                            "reasons": m["reasons"]} for m in group[:members]]})
    return result
