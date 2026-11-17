"""추천 조건 검사, 점수 계산, 설명 생성을 담당한다."""

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

WEIGHTS = {"nutrition": 0.50, "preference": 0.35, "evidence": 0.15}
GOALS = {"balance": "균형 식사", "protein": "단백질 중심", "light": "가벼운 식사"}
DISTRICTS = {"", "권선구", "영통구", "장안구", "팔달구"}
MEAT = re.compile(r"소고기|쇠고기|우삼겹|돼지|삼겹|닭|오리|족발|보쌈|스테이크|돈까스|돈가스|치킨(?!무)|베이컨|소시지|곱창|막창|갈비|육회|불고기|"
                  r"고기|제육|탕수육|꿔바로우|깐풍기|라조기|유린기|삼계탕|육개장|감자탕|뼈|곱도리|가츠|카츠|치밥|순대|선지|"
                  r"차돌|목살|항정|햄|떡갈비|육전|수육|편육|도리탕|김치찜|짜글이|돈코츠|차슈|규동|부타동|함박|미트|"
                  r"양꼬치|양고기|육사시미|두루치기|낙곱새")
ANIMAL = re.compile(r"생선|새우|해물|해산물|오징어|아귀|아구|참치|연어|멸치|젓갈|굴소스|조개|바지락|전복|장어|"
                    r"초밥|스시|사시미|물회|회덮밥|모듬회|광어|우럭|게장|꽃게|대게|킹크랩|게살|알탕|명란|낙지|쭈꾸미|주꾸미|"
                    r"문어|홍합|굴|어묵|동태|황태|코다리|대구|고등어|삼치|갈치|가자미|복어|매운탕|짬뽕")
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


MILD_WORDS = re.compile(r"안매운|맵지않|순한맛|간장|백짬뽕|하얀짬뽕|궁중떡볶이|짜장떡볶이")
HOT_WORDS = re.compile(r"매운|매콤|불닭|얼큰|핫스파이시|짬뽕|마라|고추(?!잡채)|육개장|김치찌개|부대찌개|떡볶이|제육|"
                       r"닭볶음탕|쭈꾸미|주꾸미|낙지볶음")
SOUP_WORDS = re.compile(r"국밥|해장국|찌개|설렁탕|곰탕|갈비탕|볶음탕|감자탕|매운탕|해물탕|알탕|추어탕|삼계탕|"
                        r"우동|물냉면|짬뽕|라면|라멘|칼국수|수제비|쌀국수|마라탕|샤브샤브|전골|잔치국수|온면")
# 국물 음식 이름이 들어가도 국물이 없는 조리법. 지운 뒤에 국물 음식 이름이 남는지 본다.
DRY_FORMS = re.compile(r"(볶음|비빔|짜장|쟁반)(짬뽕|우동|라면|라멘|칼국수|쌀국수|국수)|마제")
NO_SOUP_WORDS = re.compile(r"비빔|볶음|짜장|자장|덮밥|김밥|주먹밥|돈까스|돈가스|탕수육|마라샹궈|마제")
SOUP_CATEGORIES = {"찌개·탕", "국밥·해장국"}
NO_SOUP_CATEGORIES = {"피자", "치킨", "샐러드·건강식", "버거·샌드위치", "밥·덮밥"}


def taste_tags(name: str, components: list[str], category: str) -> tuple[str, str]:
    """메뉴 이름에 드러난 맵기·국물. 이름으로 알 수 없으면 빈 문자열이다.

    세트 메뉴(김밥+라면)는 국물 음식이 하나라도 있으면 국물 있음으로 본다.
    """
    signal = compact(" ".join((name, *components)))
    spice = "mild" if MILD_WORDS.search(signal) else "hot" if HOT_WORDS.search(signal) else ""
    if "국물없" in signal:
        soup = "no"
    elif category in SOUP_CATEGORIES or SOUP_WORDS.search(DRY_FORMS.sub("", signal)):
        soup = "yes"
    elif NO_SOUP_WORDS.search(signal) or category in NO_SOUP_CATEGORIES:
        soup = "no"
    else:
        soup = ""
    return spice, soup


def _combine(values: list[str], any_value: str, all_value: str) -> str:
    if any_value in values:
        return any_value
    return all_value if values and all(value == all_value for value in values) else ""


def fill_tastes(menus: list[dict[str, Any]],
                tastes: dict[str, tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    """카탈로그에 비어 있는 맵기·국물을 이름 규칙, 그다음 가게 메뉴 자료(구성 음식 이름 일치)로 채운다.

    구성 음식 중 하나라도 맵거나 국물이 있으면 그 메뉴도 그렇게 보고, 모두 안 맵거나 국물이 없을 때만
    안 매움·국물 없음으로 본다. 끝까지 알 수 없으면 빈 문자열로 둔다.
    """
    for menu in menus:
        rule_spice, rule_soup = taste_tags(menu["name"], menu["components"], menu["category"])
        spice = menu.get("spice") or rule_spice
        soup = menu.get("soup") or rule_soup
        if tastes and (not spice or not soup):
            found = [tastes.get(component, ("", "")) for component in menu["components"]]
            spice = spice or _combine([f[0] for f in found], "hot", "mild")
            soup = soup or _combine([f[1] for f in found], "yes", "no")
        menu["spice"], menu["soup"] = spice, soup
    return menus


def taste_conflict(profile: "Profile", menu: dict[str, Any]) -> bool:
    """고른 맵기·국물과 확실히 반대인 메뉴. 정보가 없는 메뉴는 빼지 않고 점수만 낮춘다."""
    spice, soup = menu.get("spice", ""), menu.get("soup", "")
    return bool((profile.spice and spice and profile.spice != spice)
                or (profile.soup and soup and profile.soup != soup))


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


def recommend(menus: list[dict[str, Any]], profile: Profile) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    check: list[dict[str, Any]] = []
    excluded = unknown = mismatched = 0
    terms = ingredient_terms(profile.allergy) | ingredient_terms(profile.exclude)
    plant = profile.diet in {"vegan", "vegetarian"}
    region = profile.district or "수원시"
    for menu in menus:
        evidence = menu["regions"].get(region)
        signal = " ".join((menu["name"], menu["category"], *menu["components"]))
        if (not evidence or compact(profile.query) not in compact(signal)
                or (profile.meal_category and menu["category"] != profile.meal_category)):
            continue
        if taste_conflict(profile, menu):
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
        for label, value, actual in (("음식군", profile.category, menu["category"]),
                                     ("맵기", profile.spice, menu["spice"]),
                                     ("국물", profile.soup, menu["soup"]),
                                     ("육식 선호", "yes" if profile.diet == "meat" else "",
                                      "yes" if meat else "")):
            if value:
                preferences.append(1 if value == actual else (0.35 if not actual else 0))
                matches.append(f"{label} {'일치' if value == actual else '정보 부족' if not actual else '불일치'}")
        n = nutrition_score(menu["nutrition"], profile.goal)
        t = sum(preferences) / len(preferences) if preferences else 0.5
        e = (0.7 * clip(math.log1p(evidence["reviews"]) / math.log1p(150))
             + 0.3 * clip(math.log1p(evidence["stores"]) / math.log1p(10)))
        score = WEIGHTS["nutrition"] * (n if n is not None else 0.30) + (
            WEIGHTS["preference"] * t + WEIGHTS["evidence"] * e)
        reasons = [f"{region} 리뷰 {evidence['reviews']:,}건 · 매장 {evidence['stores']:,}곳",
                   f"{GOALS[profile.goal]} 적합도 {round(n * 100)}점 (일반음식 참고값)"
                   if n is not None else "비교할 참고 영양값 부족", *matches]
        ranked.append({**menu, "region": region, "evidence": evidence, "reasons": reasons,
                       "score": round(score * 100, 3)})
    ranked.sort(key=lambda x: (-x["score"], -x["evidence"]["reviews"], x["standard_menu_id"]))
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
            "eligible": len(ranked), "excluded": excluded, "taste_mismatched": mismatched,
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
