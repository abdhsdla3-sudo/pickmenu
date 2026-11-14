"""추천 조건 검사, 점수 계산, 설명 생성을 담당한다."""

import math
import re
import unicodedata
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


def recommend(menus: list[dict[str, Any]], profile: Profile) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    check: list[dict[str, Any]] = []
    excluded = unknown = 0
    terms = ingredient_terms(profile.allergy) | ingredient_terms(profile.exclude)
    plant = profile.diet in {"vegan", "vegetarian"}
    region = profile.district or "수원시"
    for menu in menus:
        evidence = menu["regions"].get(region)
        signal = " ".join((menu["name"], menu["category"], *menu["components"]))
        if (not evidence or compact(profile.query) not in compact(signal)
                or (profile.meal_category and menu["category"] != profile.meal_category)):
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
    return {"items": (first_choices + alternatives)[:5], "eligible": len(ranked), "excluded": excluded,
            "unverified": unknown, "check_only": check[:3], "region": region}
