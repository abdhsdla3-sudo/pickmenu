"""추천 조건 검사, 점수 계산, 설명 생성을 담당한다."""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# 추천점수 = 100 × (영양 0.40 + 취향 0.30 + 리뷰 근거 0.10 + 리뷰 만족도 0.12 + 단골 주문 0.08) − 감점
# 자세한 기준과 바뀐 이유는 docs/score-1.6.md.
WEIGHTS = {"nutrition": 0.40, "preference": 0.30, "evidence": 0.10, "satisfaction": 0.12, "reorder": 0.08}
GOALS = {"balance": "균형 식사", "protein": "단백질 중심", "light": "가벼운 식사"}
DISTRICTS = {"", "권선구", "영통구", "장안구", "팔달구"}
# 이름에 보이는 고기·해산물 단서. 채식 제외와(참고자료 육식 라벨이 없는 메뉴의) 육식 선호 판정에 쓴다.
MEAT = re.compile(r"소고기|쇠고기|우삼겹|돼지|삼겹|닭|오리|족발|보쌈|스테이크|돈까스|돈가스|치킨(?!무)|베이컨|소시지|곱창|막창|"
                  r"갈비|육회|불고기|(?<!콩)고기|제육|탕수육|꿔바로우|깐풍기|라조기|유린기|삼계탕|육개장|감자탕|뼈|곱도리|가츠|카츠|"
                  r"치밥|순대|선지|차돌|목살|항정|햄|떡갈비|육전|수육|편육|도리탕|김치찜|짜글이|돈코츠|차슈|규동|부타동|"
                  r"함박|미트|양꼬치|양고기|육사시미|두루치기|낙곱새")
ANIMAL = re.compile(r"생선|새우|해물|해산물|오징어|아귀|아구|참치|연어|멸치|젓갈|굴소스|조개|바지락|전복|장어|초밥|스시|사시미|"
                    r"물회|회덮밥|모듬회|광어|우럭|게장|꽃게|대게|킹크랩|게살|알탕|명란|낙지|쭈꾸미|주꾸미|문어|홍합|굴|어묵|"
                    r"동태|황태|코다리|대구|고등어|삼치|갈치|가자미|복어|매운탕|짬뽕")
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
PREFERRED_FIRST = 1
# 추천점수 감점(100점 기준). 고른 맵기·국물을 음식에서 확인할 수 없으면 조건마다, 선호 음식군 음식이
# 있는데 그 밖의 음식이면 한 번 깎는다. 화면 순서는 감점까지 반영한 추천점수 순서와 같다.
UNKNOWN_TASTE_PENALTY = 15
OUTSIDE_PREFERRED_PENALTY = 25
# 같은 조건이면 늘 같은 5개가 나오지 않게, 최근 추천(브라우저가 보낸 최근 3번)에 나온 음식은
# 나온 횟수만큼(최대 2번) 깎는다. 선호 음식군 밖 감점(25점)보다 작아 선호 음식군 안에서 먼저 바뀐다.
RECENT_PENALTY = 10
RECENT_MAX = 2
# 리뷰 만족도(별점)와 단골 주문 비율의 0~1 환산 범위. 별점 범위는 추천 기준표(menu_reco)와 같다.
RATING_LOW, RATING_HIGH = 4.38, 4.83
REPEAT_LOW, REPEAT_HIGH = 0.30, 0.65
# 구 단위 만족도는 리뷰가 이만큼 있을 때만 쓰고, 적으면 수원시 전체 값을 쓴다.
MIN_QUALITY_REVIEWS = 30
# 이름 끝이 국물 요리인 음식(매운탕·어묵탕·만둣국·김치찌개·순대전골·뼈해장국 등)은 국물 있음으로 본다.
SOUP_NAME = re.compile(r"(탕|국|찌개|전골|국밥)$")
# 이름 끝이 구이·볶음·튀김 등인 음식(삼겹살·목살·제육볶음·치킨·돈가스·덮밥 등)은 국물 없음으로 본다.
DRY_NAME = re.compile(r"(구이|볶음|튀김|까스|가스|덮밥|볶음밥|김밥|주먹밥|살|바베큐|강정|치킨|윙·봉|텐더|"
                      r"가라아게|피자|버거|샌드위치|파스타|족발|보쌈|육회|회|초밥)$")


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


def column_quality(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float | None]]]:
    """컬럼별 리뷰 만족도(별점)와 단골 주문 비율. 매장 메뉴 행을 리뷰 수로 가중평균한다.

    결과: {컬럼: {"": 수원시 전체, "권선구": 구별, ...}}, 값은 {"rating", "repeat", "reviews"}.
    """
    sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for row in rows:
        column, reviews = row.get("column_name"), row.get("review_count") or 0
        if not column or not reviews:
            continue
        for area in ("", row.get("district") or ""):
            if area == "" or area in DISTRICTS:
                total = sums[(column, area)]
                if row.get("rating") is not None:
                    total[0] += float(row["rating"]) * reviews
                    total[1] += reviews
                if row.get("repeat_ratio") is not None:
                    total[2] += float(row["repeat_ratio"]) * reviews
                    total[3] += reviews
    result: dict[str, dict[str, dict[str, float | None]]] = defaultdict(dict)
    for (column, area), (rating, rated, repeat, repeated) in sums.items():
        if area and rated < MIN_QUALITY_REVIEWS:
            continue
        result[column][area] = {"rating": rating / rated if rated else None,
                                "repeat": repeat / repeated if repeated else None, "reviews": rated}
    return dict(result)


def reference_tags(columns: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """컬럼별 맵기·국물·선호음식군·리뷰 만족도. 맵기는 매장 메뉴 판정을 먼저 쓰고 없으면 컬럼표 `보통 매움`을 쓴다."""
    spice = column_spice(rows)
    quality = column_quality(rows)
    return {name: {"spice": spice.get(name) or SPICE_TABLE.get(column.get("spiciness") or "", ""),
                   "quality": quality.get(name, {}),
                   "soup": SOUP.get(column.get("broth") or "", ""),
                   # 컬럼표에서 식사가 아니거나 곁들임인 음식(만두·튀김·핫도그·주먹밥 등)
                   "side": column.get("is_meal") is False or bool(column.get("is_side")),
                   # 컬럼표 '육식'(추천 기준표의 육식 선호 대상, 고기·해산물 중심 음식). 값이 없으면 None.
                   "meat": None if column.get("meat_preferred") is None else bool(column["meat_preferred"]),
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
        soup = menu["soup"] or ("yes" if SOUP_NAME.search(food) else "no" if DRY_NAME.search(food) else "")
        if name is None:
            result.append({**menu, "soup": soup})
            continue
        tag = tags[name]
        result.append({**menu, "spice": menu["spice"] or (tag["spice"] if exact else ""),
                       "soup": soup or tag["soup"], "reference_column": name, "side": tag.get("side", False),
                       "meat_preferred": tag.get("meat"),
                       "quality": tag.get("quality", {}),
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


def quality_parts(menu: dict[str, Any], district: str) -> tuple[float | None, float | None, str]:
    """리뷰 만족도·단골 주문 점수(0~1)와 화면 설명. 구 값이 없으면 수원시 전체 값을 쓴다."""
    quality = menu.get("quality") or {}
    found = quality.get(district) or quality.get("") or {}
    rating, repeat = found.get("rating"), found.get("repeat")
    s = clip((rating - RATING_LOW) / (RATING_HIGH - RATING_LOW)) if rating is not None else None
    r = clip((repeat - REPEAT_LOW) / (REPEAT_HIGH - REPEAT_LOW)) if repeat is not None else None
    if rating is None and repeat is None:
        return None, None, "리뷰 만족도 자료 없음 · 후보 중앙값으로 계산"
    text = " · ".join(part for part in (
        f"리뷰 만족도 {rating:.2f}점" if rating is not None else "",
        f"단골 주문 {round(repeat * 100)}%" if repeat is not None else "") if part)
    return s, r, text + (f" ({district})" if district and district in quality else "")


def median(values: list[float], default: float = 0.5) -> float:
    ordered = sorted(values)
    if not ordered:
        return default
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def recommend(menus: list[dict[str, Any]], profile: Profile, recent: Sequence[str] = ()) -> dict[str, Any]:
    """`recent`: 최근 추천에서 본 대표 음식 이름(중복은 본 횟수). 본 만큼 조금 뒤로 보낸다."""
    seen_count = Counter(compact(name) for name in recent)
    ranked: list[dict[str, Any]] = []
    check: list[dict[str, Any]] = []
    excluded = unknown = mismatched = 0
    terms = ingredient_terms(profile.allergy) | ingredient_terms(profile.exclude)
    plant = profile.diet in {"vegan", "vegetarian"}
    region = profile.district or "수원시"
    query = compact(profile.query)
    for menu in menus:
        evidence = menu["regions"].get(region)
        groups = menu.get("groups") or [menu["category"]]
        if not evidence or (profile.meal_category and profile.meal_category not in groups):
            continue
        signal = " ".join((menu["name"], menu["category"], *menu["components"]))
        normalized_signal = compact(signal) if query or terms else ""
        if query and query not in normalized_signal:
            continue
        # 컬럼표에서 식사가 아니거나 곁들임인 음식은 검색어로 직접 찾을 때만 보여 준다.
        if menu.get("side") and not profile.query:
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
        # 육식 선호 일치는 참고자료 컬럼표의 '육식' 라벨(menu_reco와 같은 기준)을 먼저 쓰고, 연결된 컬럼이
        # 없을 때만 이름 단서를 쓴다. 채식 제외는 라벨이나 이름 단서 중 하나라도 고기·해산물이면 뺀다.
        label = menu.get("meat_preferred")
        named_meat = bool(MEAT.search(signal))
        meat = label if label is not None else named_meat
        conflict = any(term in normalized_signal for term in terms) or (
            plant and (named_meat or label is True or ANIMAL.search(signal) or
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
        s, r, quality_text = quality_parts(menu, profile.district)
        reasons = [f"{region} 리뷰 {evidence['reviews']:,}건 · 매장 {evidence['stores']:,}곳",
                   f"{GOALS[profile.goal]} 적합도 {round(n * 100)}점 (일반음식 참고값)"
                   if n is not None else "비교할 참고 영양값 부족", quality_text, *matches]
        ranked.append({**menu, "region": region, "evidence": evidence, "reasons": reasons,
                       "_parts": (n, t, e, s, r), "_preferred": bool(profile.category) and profile.category in groups,
                       "_unknown_taste": sum(1 for value, actual in (
                           (profile.spice, menu.get("family_spice", menu["spice"])),
                           (profile.soup, menu.get("family_soup", menu["soup"]))) if value and not actual)})
    # 영양 자료가 없는 메뉴는 감점하지 않고 이번 후보들의 영양 점수 중앙값을 쓴다.
    middle = median([menu["_parts"][0] for menu in ranked if menu["_parts"][0] is not None])
    # 만족도·단골 주문 자료가 없는 음식도 같은 방식으로 후보 중앙값을 쓴다.
    middle_s = median([menu["_parts"][3] for menu in ranked if menu["_parts"][3] is not None])
    middle_r = median([menu["_parts"][4] for menu in ranked if menu["_parts"][4] is not None])
    # 선호 음식군에 맞는 음식이 하나라도 있으면 그 밖의 음식은 감점한다.
    preferred_foods = len({compact(menu["components"][0]) for menu in ranked if menu["_preferred"]})
    preferred_first = preferred_foods >= PREFERRED_FIRST
    for menu in ranked:
        n, t, e, s, r = menu.pop("_parts")
        score = 100 * (WEIGHTS["nutrition"] * (n if n is not None else middle)
                       + WEIGHTS["preference"] * t + WEIGHTS["evidence"] * e
                       + WEIGHTS["satisfaction"] * (s if s is not None else middle_s)
                       + WEIGHTS["reorder"] * (r if r is not None else middle_r))
        unknown_taste = menu.pop("_unknown_taste")
        outside = preferred_first and not menu.pop("_preferred")
        menu.pop("_preferred", None)
        times = min(RECENT_MAX, seen_count[compact(menu["components"][0])])
        penalty = (UNKNOWN_TASTE_PENALTY * unknown_taste + (OUTSIDE_PREFERRED_PENALTY if outside else 0)
                   + RECENT_PENALTY * times)
        menu["score"] = round(max(0.0, score - penalty), 3)
        # 메뉴 상세에서 감점 이유를 볼 수 있게 남긴다.
        if n is None:
            menu["reasons"][1] = f"참고 영양값 없음 · 후보 중앙값 {round(middle * 100)}점으로 계산"
        if unknown_taste:
            menu["reasons"].append(f"고른 맵기·국물을 확인할 수 없어 {UNKNOWN_TASTE_PENALTY * unknown_taste}점 감점")
        if outside:
            menu["reasons"].append(f"선호 음식군 밖이라 {OUTSIDE_PREFERRED_PENALTY}점 감점")
        if times:
            menu["reasons"].append(f"최근 추천에 {times}번 나와 {RECENT_PENALTY * times}점 감점 (다른 메뉴도 보여 드려요)")
        menu["_unknown_nutrition"] = n is None
    # 점수가 같으면 영양 자료가 있는 메뉴를 앞에 둔다.
    ranked.sort(key=lambda x: (-x["score"], x["_unknown_nutrition"], -x["evidence"]["reviews"], x["standard_menu_id"]))
    for menu in ranked:
        del menu["_unknown_nutrition"]
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
            "category": profile.category, "preferred_foods": preferred_foods,
            "recent_applied": sum(1 for food in {compact(m["components"][0]) for m in ranked} if seen_count[food]),
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
