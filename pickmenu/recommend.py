"""추천 조건 검사, 점수 계산, 설명 생성을 담당한다."""

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path
from typing import Any

# 추천점수 = 100 × (영양 0.40 + 취향 0.30 + 리뷰 근거 0.10 + 리뷰 만족도 0.12 + 단골 주문 0.08) − 감점
# 자세한 기준과 바뀐 이유는 docs/score-1.6.md.
WEIGHTS = {"nutrition": 0.40, "preference": 0.30, "evidence": 0.10, "satisfaction": 0.12, "reorder": 0.08}
GOALS = {"balance": "균형 식사", "protein": "단백질 중심", "light": "가벼운 식사"}
DISTRICTS = {"", "권선구", "영통구", "장안구", "팔달구"}
MEAT = re.compile(r"소고기|쇠고기|우삼겹|돼지|삼겹|닭|오리|족발|보쌈|스테이크|돈까스|돈가스|치킨|베이컨|소시지|곱창|막창|갈비|육회|불고기")
ANIMAL = re.compile(r"생선|새우|해물|해산물|오징어|아귀|아구|참치|연어|멸치|젓갈|굴소스|조개|바지락|전복|장어")
# 채식·비건일 때만 쓰는 추가 충돌 단서. 보통 고기·해산물 육수나 건더기가 들어가는 음식 이름이다.
# MEAT는 육식 선호 점수에도 쓰여 따로 둔다. 여기 없는 메뉴도 안전하다는 뜻은 아니다('재료 확인 필요').
PLANT_CONFLICT = re.compile(
    r"제육|목살|두루치기|순대|감자탕|뼈|곰탕|설렁탕|내장|국밥|해장국|선지|육개장|사태|대창|삼계|부대|햄|스팸|"
    r"(?<!콩)고기|가츠|카츠|깐풍|탕수|육사시미|깐풍육|가라아게|치밥|짜장|짜글이|김치찜|김치찌개|고추장찌개|만두|만둣|"
    r"라멘|마제소바|탄탄면|유산슬|양장피|팔보채|짬뽕|마라|똠얌|알탕|알곤이|알밥|매운탕|어묵|오뎅|북엇|북어|"
    r"황태|동태|코다리|명란|낙지|쭈꾸미|주꾸미|문어|오삼|꽃게|대게|홍합|초밥|마키|사시미|포케|물회|회덮밥|"
    r"(?:^|\s)회(?:\s|$)|카레|커리|팟타이|피시|어육|멸치|육수|쌀국수|초계|물냉면|(?:^|\s)냉면(?:\s|$)")
# 알레르기 재료가 보통 들어가는 음식 이름. 재료 이름이 메뉴명에 없어도 충돌로 보고 뺀다.
ALLERGEN_DISHES = {
    "밀": r"면|국수|빵|피자|파스타|우동|라면|만두|만둣|수제비|튀김|돈가스|돈까스|토스트|샌드위치|햄버거|떡볶이|라볶이|"
          r"짜장|짬뽕|쫄면|소바|라멘|가츠|카츠|탕수|부침|파전|전병|와플|케이크",
    "우유": r"피자|크림|로제|리조또|라떼|그라탕|치즈",
    "달걀": r"오므라이스|계란|달걀|에그|마요|카스테라|빵|케이크",
    "새우": r"짬뽕|해물|팟타이|똠얌|새우",
    "대두": r"된장|청국장|간장|두부|콩",
}
# 맛 선호. 메뉴 자료에 맛 정보가 없어 메뉴 이름·분류·구성 음식 이름의 단서로 짐작한다(맵기·국물은 따로 있다).
# 키: (화면 이름, 메뉴 단서). 단서가 없는 메뉴는 '정보 부족'으로 점수만 낮춘다.
TASTES = {
    "sweet": ("달콤한 맛", r"떡볶이|라볶이|로제|양념치킨|강정|탕수|불고기|찜닭|데리야끼|허니|꿀|팬케이크|와플|디저트|빙수|케이크|"
                         r"짜장|갈비|고구마|단호박|토스트|마요"),
    "salty": ("짭짤한 맛", r"찌개|조림|젓갈|간장|라면|짜장|두루치기|제육|된장|청국장|부대|라멘|게장|짜글이|볶음|치킨|족발"),
    "sour": ("새콤한 맛", r"냉면|물회|쫄면|비빔국수|무침|초계|막국수|샐러드|포케|탕수|깐풍|유린기|똠얌|김치말이|레몬|유자|"
                        r"피클|초밥|쟁반국수"),
    "nutty": ("고소한 맛", r"들깨|참깨|깨|콩국수|땅콩|크림|치즈|버터|견과|두부|비지|순두부|부침|파전|김밥|볶음밥|비빔밥|"
                         r"전복죽|호박죽|곱창|대창"),
    "plain": ("담백한 맛", r"죽|곰탕|설렁탕|백숙|삼계|칼국수|수제비|샤브|초밥|(?:^|\s)회(?:\s|$)|샐러드|포케|우동|소바|"
                         r"두부|수육|보쌈|콩나물|북엇|황태|맑은|지리|쌀국수|닭가슴살"),
    "rich": ("진하고 기름진 맛", r"크림|치즈|버터|튀김|치킨|돈가스|돈까스|까스|피자|햄버거|곱창|막창|대창|삼겹|마요|로제|"
                            r"리조또|탕수|그라탕|라멘|감자탕|부대"),
    "crispy": ("바삭한 식감", r"튀김|치킨|돈가스|돈까스|까스|가라아게|탕수|깐풍|부침|파전|강정|크리스피|후라이드|유린기|"
                           r"(?:^|\s)전(?:\s|$)"),
    "cool": ("시원한 맛", r"냉면|물회|콩국수|막국수|김치말이|초계|해장국|콩나물|북엇|황태|동태|매운탕|조개|재첩|쌀국수|"
                        r"메밀|소바|지리"),
    "chewy": ("쫄깃한 식감", r"쫄면|떡|냉면|곱창|막창|족발|오징어|쭈꾸미|주꾸미|낙지|우동|칼국수|수제비|순대"),
    "soft": ("부드러운 식감", r"죽|계란|달걀|오므라이스|두부|순두부|찜|수육|보쌈|리조또|크림|그라탕|스튜|카레|커리"),
}
# 조리법은 식감 평가와 별개인 기름 사용 단서다. 끓이기·굽기·볶기는 기름 양을 추정하지 않는다.
COOKING_EFFECT = {"튀기기": -1, "찌기": 1, "삶기": 1, "데치기": 1, "굽기": 0, "볶기": 0, "끓이기": 0}
COOKING_NAME = {
    "튀기기": r"튀김|튀긴|후라이드|프라이드|돈가스|돈까스|돈카츠|탕수육|가라아게",
    "찌기": r"^찐[가-힣]|쪄낸|찜기|증기찜|계란찜|달걀찜",
    "삶기": r"삶은|삶아서",
    "데치기": r"데친|데쳐낸",
    "굽기": r"구운|구이|오븐에구운",
    "볶기": r"볶음|볶은",
    "끓이기": r"끓인|끓여낸",
}
VEGAN_EXCLUDE = re.compile(r"달걀|계란|우유|치즈|버터|유청|요거트|꿀|피자|크림|로제|리조또|오므라이스|토스트|마요")
# 이야기 추천에서 통째로 뺄 수 있는 음식 묶음(면·밀가루·튀김·돼지고기 등). 팀이 고칠 수 있게 자료 파일로 둔다.
STORY_GROUPS: dict[str, dict[str, list[str]]] = {
    name: rule for name, rule in json.loads(Path(__file__).with_name("story_groups.json").read_text(encoding="utf-8")).items()
    if not name.startswith("_")}
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
    # 맛 선호(TASTES 키, 쉼표 구분). 앞에 '-'가 붙으면 피하고 싶은 맛.
    taste: str = ""
    avoid_menus: str = ""
    # 뺄 음식군(쉼표 구분, 목록 값)과 뺄 음식 묶음(STORY_GROUPS 이름). 이야기 추천에서 "중식 말고", "면 빼 줘"로 정한다.
    exclude_categories: str = ""
    exclude_groups: str = ""

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
        for field, maximum in {"query": 40, "exclude": 100, "allergy": 100, "avoid_menus": 300}.items():
            value = data.get(field, "")
            if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
                raise ValueError(f"입력은 {maximum}자 이내로 작성해 주세요.")
            if field != "query" and value.strip() and not re.search(r"[^,;/\s]", value):
                raise ValueError("제외할 재료나 메뉴 이름을 입력해 주세요.")
            values[field] = value.strip()
        taste = data.get("taste", "")
        if not isinstance(taste, str) or any(item.lstrip("-") not in TASTES
                                            for item in taste.split(",") if item):
            raise ValueError("목록에 있는 조건을 선택해 주세요.")
        values["taste"] = taste
        for field, allowed in (("exclude_categories", set(categories)), ("exclude_groups", set(STORY_GROUPS))):
            value = data.get(field, "")
            if not isinstance(value, str) or any(item not in allowed for item in value.split(",") if item):
                raise ValueError("목록에 있는 조건을 선택해 주세요.")
            values[field] = value
        return cls(**values)


def ingredient_terms(value: str) -> set[str]:
    terms = {compact(term) for term in re.split(r"[,;/]", value) if term.strip()}
    for group in ALIASES:
        if terms.intersection(group):
            terms.update(group)
    return terms


def clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def reference_fat(nutrition: dict[str, Any] | None) -> float | None:
    if not nutrition or nutrition["basis"] != 100 or nutrition["unit"] != "g":
        return None
    return nutrition["values"][3]


def cooking_signal(menu: dict[str, Any]) -> tuple[str, str]:
    method = menu.get("cooking_method")
    if method in COOKING_EFFECT and not menu.get("compound") and len(menu["components"]) == 1:
        return method, "공개 참고 레시피"
    name = compact(menu["components"][0])
    hints = [method for method, pattern in COOKING_NAME.items() if re.search(pattern, name)]
    return (hints[0], "대표 메뉴명 추정") if len(hints) == 1 else ("", "")


def less_greasy(menu: dict[str, Any], rich_name: bool) -> tuple[float, str]:
    fat = reference_fat(menu["nutrition"])
    method, source = cooking_signal(menu)
    if fat is None:
        score = 0.0 if rich_name and not COOKING_EFFECT.get(method) else 0.35
        note = "지방 참고값 없음"
    else:
        # 20g은 점수 환산 범위다. 비교 가능한 수치가 있으면 이 점수가 우선한다.
        score = clip(1 - fat / 20)
        note = f"지방 100g당 {fat:g}g (일반음식 참고값)"
    if method:
        weight = 0.10 if fat is not None else 0.25
        if source == "대표 메뉴명 추정":
            weight *= 0.5
        score += weight * COOKING_EFFECT[method]
        note += f" · {method} ({source})"
        if not COOKING_EFFECT[method]:
            note += " · 기름 사용량은 알 수 없음"
    elif fat is None:
        note += " · 메뉴 이름 단서만 참고"
    return clip(score), note


def serving_grams(nutrition: dict[str, Any]) -> float | None:
    portion = str(nutrition.get("serving_reference") or "").strip()
    grams = re.fullmatch(r"(?:1인분\s*\(?)?(\d+(?:\.\d+)?)\s*g\)?", portion, re.I)
    return float(grams[1]) if grams and float(grams[1]) > 0 else None


def nutrition_score(nutrition: dict[str, Any] | None, goal: str) -> float | None:
    if not nutrition or nutrition["basis"] != 100 or nutrition["unit"] != "g":
        return None
    kcal, protein, carbs, fat, sodium = nutrition["values"]
    if goal == "protein":
        return clip(protein / 12) if protein is not None else None
    if goal == "light":
        if kcal is None:
            return None
        # 공공 자료의 명시된 g 단위 1회 참고량만 쓰고, 기준량(100g)을 한 끼로 보지 않는다.
        if (grams := serving_grams(nutrition)) is not None:
            ratio = grams / 100
            energy_score = clip(1 - (kcal * ratio - 200) / 550)
            sodium_score = clip(1 - sodium * ratio / 1800) if sodium is not None else None
            return 0.7 * energy_score + 0.3 * sodium_score if sodium_score is not None else min(0.85, energy_score)
        energy_score = clip(1 - (kcal - 80) / 220)
        if sodium is None:
            return min(0.85, energy_score)
        return min(0.9, 0.7 * energy_score + 0.3 * clip(1 - sodium / 750))
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
# 추천점수 감점(100점 기준). 고른 맵기·국물을 음식에서 확인할 수 없으면 조건마다, 선호 음식군 음식이
# 있는데 그 밖의 음식이면 한 번 깎는다. 검색 이름과 선호 음식군 우선순위가 같을 때 점수순으로 정렬한다.
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
KNOWN_HOT = re.compile(r"고추바사삭|불족발|부대찌개")
OPTION_ONLY = re.compile(r"(?:곱빼기|대곱빼기|사리|면|밥|공깃밥|토핑|치즈|소스|고기|양|육수)"
                         r"(?:변경|추가|선택|업그레이드)(?:[+＋]?[0-9,]+원?)?")
SOUP_NAME = re.compile(r"(탕|국|찌개|전골|국밥)$")
# 이름 끝이 구이·볶음·튀김 등인 음식(삼겹살·목살·제육볶음·치킨·돈가스·덮밥 등)은 국물 없음으로 본다.
DRY_NAME = re.compile(r"(구이|볶음|튀김|까스|가스|덮밥|볶음밥|김밥|주먹밥|살|바베큐|강정|치킨|윙·봉|텐더|"
                      r"가라아게|피자|버거|샌드위치|파스타|족발|보쌈|육회|회|초밥)$")


def column_spice(rows: list[dict[str, Any]]) -> dict[str, str]:
    """참고자료 매장 메뉴 행의 맵기(안 매움·약간 매움·매움·일부 매운 옵션)를 컬럼별로 모은다.

    판정이 있는 행이 3건 이상일 때만 정한다. `매움`·`약간 매움`이 절반 이상이면 hot, `안 매움`이
    (`일부 매운 옵션`을 뺀 행의) 80% 이상이면 mild, 섞여 있으면 정하지 않는다. `일부 매운 옵션`은 기본 메뉴는
    안 맵고 매운 선택지가 따로 있다는 뜻이라 mild 판단에서는 빼고 센다(햄버거 안 매움 20·옵션 10·약간 2 → mild).
    `명시 없음`은 세지 않는다.
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
        if count["매움"] + count["약간 매움"] >= 0.5 * total:
            result[column] = "hot"
        elif count["안 매움"] >= 0.8 * (total - count["일부 매운 옵션"]):
            result[column] = "mild"
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
    국물 있음으로 본다. 끝만 같은 컬럼(로제떡볶이 → 떡볶이)에서는 메뉴 단위로는 국물·음식군만 가져오고,
    맵기는 대표 메뉴 단위에서 다른 근거가 없을 때만 near_spice 규칙으로 빌린다(food_tags). 음식군은 카탈로그 음식군 하나에 컬럼의 선호음식군(예: 짬뽕 → 중식·면)을 더한다.
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
        spice = menu["spice"] or ("hot" if KNOWN_HOT.search(compact(food + menu["name"])) else "")
        if name is None:
            result.append({**menu, "soup": soup, "spice": spice})
            continue
        tag = tags[name]
        result.append({**menu, "spice": spice or (tag["spice"] if exact else ""),
                       "soup": soup or tag["soup"], "reference_column": name, "side": tag.get("side", False),
                       "meat_preferred": tag.get("meat"),
                       "quality": tag.get("quality", {}),
                       "groups": list(dict.fromkeys([menu["category"], *tag["groups"]]))})
    return food_tags(result, tags, found)


# 끝이 같은 컬럼(김치볶음밥 → 볶음밥)의 맵기를 빌려 쓸 때, 앞에 붙은 말이 맵기를 바꾸면 빌리지 않는다.
MILDER = re.compile(r"로제|크림|까르보|간장|치즈|짜장|궁중|순한|안매운|허니|버터|마요|데리야끼")
HOTTER = re.compile(r"매운|매콤|불|마라|김치|짬뽕|고추|얼큰|칼칼|핫|청양|양념|닭갈비|낙지|쭈꾸미|주꾸미|제육")


# 여러 음식을 묶은 일반 컬럼. 이런 컬럼의 맵기는 개별 음식(계란덮밥·제육덮밥)에 빌려 쓰지 않는다.
GENERIC_COLUMNS = {"덮밥", "볶음", "찌개", "탕", "찜", "구이", "전골", "국수", "면", "밥", "정식", "세트", "튀김", "전"}


def near_spice(food: str, column: str, column_spice: str) -> str:
    """대표 메뉴 이름과 끝만 같은 참고자료 컬럼의 맵기. 일반 컬럼이거나 이름 앞부분이 맵기를 바꿀 수
    있으면 비워 둔다(로제떡볶이는 떡볶이의 hot을, 김치볶음밥은 볶음밥의 mild를 빌리지 않는다)."""
    if column in GENERIC_COLUMNS:
        return ""
    if column_spice == "hot" and MILDER.search(food) or column_spice == "mild" and HOTTER.search(food):
        return ""
    return column_spice


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
            if not value and key == "spice" and name and not exact:
                value = near_spice(food, name, tags[name]["spice"])
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


def recommend(menus: list[dict[str, Any]], profile: Profile, recent: Sequence[str] = (), *,
              conversation: bool = False) -> dict[str, Any]:
    """`recent`: 최근 추천에서 본 대표 음식 이름(중복은 본 횟수). 본 만큼 조금 뒤로 보낸다."""
    seen_count = Counter(compact(name) for name in recent)
    ranked: list[dict[str, Any]] = []
    check: list[dict[str, Any]] = []
    excluded = unknown = mismatched = 0
    allergy_terms = ingredient_terms(profile.allergy)
    terms = allergy_terms | ingredient_terms(profile.exclude)
    allergy_dishes = "|".join(dishes for name, dishes in ALLERGEN_DISHES.items() if name in allergy_terms)
    plant = profile.diet in {"vegan", "vegetarian"}
    # 메뉴 재료 정보가 거의 없어, 이름·구성에서 충돌이 확인된 메뉴만 빼고 나머지는 추천한다.
    # 싫어하는 재료는 안전 문제가 아니라 그대로 두고, 알레르기·채식은 '재료 확인 필요'로 표시한다.
    unchecked = ([f"알레르기({profile.allergy})"] if allergy_terms else []) + (
        ["비건" if profile.diet == "vegan" else "채식"] if plant else [])
    check_note = (f"재료 정보가 없어 {'·'.join(unchecked)} 조건을 확인하지 못했어요. 주문 전에 가게에 꼭 확인해 주세요."
                  if unchecked else "")
    region = profile.district or "수원시"
    query = compact(profile.query)
    avoided = {compact(name) for name in re.split(r"[,;/]", profile.avoid_menus) if name.strip()}
    excluded_categories = {name for name in profile.exclude_categories.split(",") if name}
    group_rules = [STORY_GROUPS[name] for name in profile.exclude_groups.split(",") if name]
    excluded_categories.update(category for rule in group_rules for category in rule["categories"])
    group_names = (re.compile("|".join(re.escape(compact(word)) for rule in group_rules for word in rule["names"]))
                   if group_rules else None)
    for menu in menus:
        if OPTION_ONLY.fullmatch(compact(menu["name"])) or OPTION_ONLY.fullmatch(compact(menu["components"][0])):
            continue
        if avoided and any(compact(name) in avoided for name in menu["components"]):
            excluded += 1
            continue
        evidence = menu["regions"].get(region)
        groups = menu.get("groups") or [menu["category"]]
        if not evidence or (profile.meal_category and profile.meal_category not in groups):
            continue
        if excluded_categories.intersection(groups) or (
                group_names and group_names.search(compact(menu["name"] + " " + " ".join(menu["components"])))):
            excluded += 1
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
        # 이야기에서 국물 조건을 썼으면 국물 여부를 확인할 수 있는 메뉴만 남긴다.
        name_spice = menu["spice"] or ("hot" if KNOWN_HOT.search(compact(menu["components"][0] + menu["name"])) else "")
        family_soup = menu.get("family_soup") or menu["soup"]
        if any(value and actual and value != actual
               for value, actual in ((profile.spice, name_spice), (profile.soup, menu["soup"]),
                                     (profile.spice, menu.get("family_spice", "")),
                                     (profile.soup, family_soup))) or (conversation and profile.soup and not family_soup):
            mismatched += 1
            continue
        # 육식 선호 일치는 참고자료 컬럼표의 '육식' 라벨(menu_reco와 같은 기준)을 먼저 쓰고, 연결된 컬럼이
        # 없을 때만 이름 단서를 쓴다. 채식 제외는 라벨·이름 단서·채식 충돌 음식 이름 중 하나라도 걸리면 뺀다.
        label = menu.get("meat_preferred")
        named_meat = bool(MEAT.search(signal))
        meat = label if label is not None else named_meat
        conflict = any(term in normalized_signal for term in terms) or bool(
            allergy_dishes and re.search(allergy_dishes, signal)) or (
            plant and (named_meat or label is True or ANIMAL.search(signal) or PLANT_CONFLICT.search(signal) or
                       (profile.diet == "vegan" and VEGAN_EXCLUDE.search(signal))))
        if conflict:
            excluded += 1
            continue
        if check_note:
            unknown += 1
            check.append({"standard_menu_id": menu["standard_menu_id"], "name": menu["name"],
                          "review_count": evidence["reviews"]})
        matches: list[str] = []
        preferences: list[float] = []
        category = profile.category if profile.category in groups else menu["category"]
        for label, value, actual in (("음식군", profile.category, category),
                                     # 맵기·국물 점수는 음식 자체의 값으로 매긴다. 매운오뎅김밥 하나로
                                     # 김밥이 매운 음식 1위가 되지 않게, 음식 값이 미정이면 정보 부족으로 본다.
                                     ("맵기", profile.spice, menu.get("family_spice", menu["spice"])),
                                     ("국물", profile.soup, family_soup),
                                     ("육식 선호", "yes" if profile.diet == "meat" else "",
                                      "yes" if meat else "")):
            if value:
                preferences.append(1 if value == actual else (0.35 if not actual else 0))
                matches.append(f"{label} {'일치' if value == actual else '정보 부족' if not actual else '불일치'}")
        # 이름 단서는 세트 메뉴명이 아닌 대표 음식명과 분류를 본다.
        taste_signal = f"{menu['components'][0]} {menu['category']}"
        for item in filter(None, profile.taste.split(",")):
            name, clue = TASTES[item.lstrip("-")]
            found = bool(re.search(clue, taste_signal))
            if item == "-rich":
                score, note = less_greasy(menu, found)
                preferences.append(score)
                matches.append(note)
            elif item.startswith("-"):
                preferences.append(0 if found else 1)
                matches.append(f"{name} 피하기 {'불일치' if found else '일치'} (메뉴 이름으로 짐작)")
            else:
                preferences.append(1 if found else 0.35)
                matches.append(f"{name} {'단서 있음' if found else '정보 부족'} (메뉴 이름으로 짐작)")
        n = nutrition_score(menu["nutrition"], profile.goal)
        t = sum(preferences) / len(preferences) if preferences else 0.5
        e = (0.7 * clip(math.log1p(evidence["reviews"]) / math.log1p(150))
             + 0.3 * clip(math.log1p(evidence["stores"]) / math.log1p(10)))
        s, r, quality_text = quality_parts(menu, profile.district)
        basis = "일반음식 참고값"
        if profile.goal == "light" and n is not None:
            basis = "1인분 일반음식 참고값" if serving_grams(menu["nutrition"]) is not None else "100g 일반음식 참고값 · 1인분 정보 없음"
        reasons = [f"{region} 이 메뉴 리뷰 {evidence['reviews']:,}건 · 매장 {evidence['stores']:,}곳",
                   f"{GOALS[profile.goal]} 적합도 {round(n * 100)}점 ({basis})"
                   if n is not None else "비교할 참고 영양값 부족", quality_text, *matches]
        if check_note:
            reasons.append(check_note)
        ranked.append({**menu, "region": region, "evidence": evidence, "reasons": reasons,
                       "needs_check": bool(check_note),
                       "_parts": (n, t, e, s, r), "_preferred": bool(profile.category) and profile.category in groups,
                       "_unknown_taste": sum(1 for value, actual in (
                           (profile.spice, menu.get("family_spice", menu["spice"])),
                           (profile.soup, family_soup)) if value and not actual)})
    # 영양 자료가 없는 메뉴는 감점하지 않고 이번 후보들의 영양 점수 중앙값을 쓴다.
    middle = median([menu["_parts"][0] for menu in ranked if menu["_parts"][0] is not None])
    # 만족도·단골 주문 자료가 없는 음식도 같은 방식으로 후보 중앙값을 쓴다.
    middle_s = median([menu["_parts"][3] for menu in ranked if menu["_parts"][3] is not None])
    middle_r = median([menu["_parts"][4] for menu in ranked if menu["_parts"][4] is not None])
    # 선호 음식군에 맞는 음식이 하나라도 있으면 그 밖의 음식은 감점한다.
    preferred_foods = len({compact(menu["components"][0]) for menu in ranked if menu["_preferred"]})
    preferred_first = bool(preferred_foods)
    for menu in ranked:
        n, t, e, s, r = menu.pop("_parts")
        score = 100 * (WEIGHTS["nutrition"] * (n if n is not None else middle)
                       + WEIGHTS["preference"] * t + WEIGHTS["evidence"] * e
                       + WEIGHTS["satisfaction"] * (s if s is not None else middle_s)
                       + WEIGHTS["reorder"] * (r if r is not None else middle_r))
        unknown_taste = menu.pop("_unknown_taste")
        outside = preferred_first and not menu.pop("_preferred")
        menu["_outside"] = outside
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
    # 검색어가 있으면 대표 음식명이 검색어와 같은 메뉴 → 검색어로 끝나는 대표 음식(양념치킨: 치킨의 한 종류)
    # → 검색어가 든 다른 대표 음식(치킨마요덮밥) → 나머지(세트 메뉴 이름에만 든 경우) 순으로 둔다.
    def name_tier(menu: dict[str, Any]) -> int:
        name = compact(menu["components"][0])
        if not query or name == query:
            return 0
        return 1 if name.endswith(query) else 2 if query in name else 3

    ranked.sort(key=lambda x: (name_tier(x), not conversation and x["_outside"], -x["score"], x["_unknown_nutrition"],
                               -x["evidence"]["reviews"],
                               x["standard_menu_id"]))
    for menu in ranked:
        del menu["_unknown_nutrition"], menu["_outside"]
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


# 결과가 0개일 때 하나씩 빼 보는 조건(앞에서부터). 국밥에 '국물 없음'처럼 서로 맞지 않는 조합이나
# 그 지역에 메뉴가 없는 경우에 빈 화면 대신 조건을 넓혀 보여 주고 무엇을 뺐는지 알린다.
# 안 매운 쪽·식단·알레르기·기피 재료·검색어·음식군은 빼지 않는다.
RELAXABLE = (("soup", "국물 조건"), ("spice", "맵기 조건"), ("district", "지역(수원시 전체로 넓힘)"))


def recommend_relaxed(menus: list[dict[str, Any]], profile: Profile, recent: Sequence[str] = (), *,
                      conversation: bool = False) -> tuple[dict[str, Any], Profile]:
    """recommend와 같고, 결과가 없거나 말한 메뉴 자체가 없으면 RELAXABLE 조건을 적게 빼는 것부터 차례로 빼서
    다시 찾는다. (결과, 실제로 쓴 조건)"""
    query = compact(profile.query)

    def good(result: dict[str, Any]) -> bool:
        # 검색어가 메뉴 이름이면 그 메뉴(또는 그 종류, 예: 양념치킨)가 1위여야 한다.
        if not result["families"]:
            return False
        first = compact(result["families"][0]["name"])
        return not query or not any(query == compact(m["components"][0]) for m in menus) or first.endswith(query)

    result = recommend(menus, profile, recent, conversation=conversation)
    result["relaxed"] = []
    if good(result):
        return result, profile
    fields = [(field, label) for field, label in RELAXABLE
              if getattr(profile, field) and not (field == "spice" and profile.spice == "mild")
              and not (field == "soup" and conversation)]
    for size in range(1, len(fields) + 1):
        for chosen in combinations(fields, size):
            current = replace(profile, **{field: "" for field, _ in chosen})
            retry = recommend(menus, current, recent, conversation=conversation)
            if good(retry):
                retry["relaxed"] = [label for _, label in chosen]
                return retry, current
    # 이야기 추천에서 대표 메뉴 이름이 아닌 검색어("치팅", "적당히 든든한")만으로 0개가 되면 검색어를 뺀다.
    if conversation and query and not result["families"] and not any(
            query == compact(m["components"][0]) for m in menus):
        current = replace(profile, query="")
        retry = recommend(menus, current, recent, conversation=conversation)
        if retry["families"]:
            retry["relaxed"] = [f"검색어({profile.query})"]
            return retry, current
    return result, profile


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
