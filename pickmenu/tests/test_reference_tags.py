import unittest
from typing import Any
from unittest.mock import patch

import menu_reco
from recommend import (
    Profile,
    apply_reference,
    column_spice,
    match_column,
    recommend,
    reference_tags,
)


def menu(name, category="중식", spice="", soup="", menu_id="MN_1", food=None):
    return {"name": name, "standard_menu_id": menu_id, "category": category, "components": [food or name],
            "spice": spice, "soup": soup, "nutrition": None,
            "regions": {"수원시": {"reviews": 30, "stores": 3}}}


COLUMNS: dict[str, dict[str, Any]] = {"짬뽕": {"groups": "중식;면", "broth": "있음", "spiciness": "보통 매움"},
           "볶음짬뽕": {"groups": "중식", "broth": "있음", "spiciness": "-"},
           "탕수육": {"groups": "중식", "broth": "애매", "spiciness": "-"},
           "피자": {"groups": "피자", "broth": "없음", "spiciness": "-"},
           "떡볶이": {"groups": "분식", "broth": "애매", "spiciness": "보통 매움"},
           "아구찜·해물찜": {"groups": "찜·조림;해산물", "broth": "없음", "spiciness": "보통 매움"},
           "어묵": {"groups": "분식", "broth": "없음", "spiciness": "-"},
           "만두": {"groups": "분식", "broth": "없음", "spiciness": "-", "is_meal": True, "is_side": True}}
ROWS = ([{"column_name": "피자", "spiciness": "안 매움"}] * 9 + [{"column_name": "피자", "spiciness": "약간 매움"}]
        + [{"column_name": "탕수육", "spiciness": "안 매움"}, {"column_name": "탕수육", "spiciness": "일부 매운 옵션"}]
        + [{"column_name": "짬뽕", "spiciness": "매움"}] * 3 + [{"column_name": "짬뽕", "spiciness": "명시 없음"}]
        + [{"column_name": "떡볶이", "spiciness": "안 매움"}] * 2)  # 판정 2건은 정하지 않는다
TAGS = reference_tags(COLUMNS, ROWS)


class ReferenceTagTest(unittest.TestCase):
    def test_column_spice_uses_store_menu_levels(self):
        self.assertEqual(column_spice(ROWS), {"피자": "mild", "짬뽕": "hot"})  # 탕수육은 섞여 있어 미정
        self.assertEqual((TAGS["피자"]["spice"], TAGS["탕수육"]["spice"], TAGS["떡볶이"]["spice"]), ("mild", "", "hot"))

    def test_match_column(self):
        names = sorted(COLUMNS)
        self.assertEqual(match_column("짬뽕", names), ("짬뽕", True))
        self.assertEqual(match_column("아구찜", names), ("아구찜·해물찜", True))
        self.assertEqual(match_column("로제떡볶이", names), ("떡볶이", False))
        self.assertEqual(match_column("마파두부", names), (None, False))

    def test_fills_only_blank_tags_from_reference(self):
        jjamppong, fried, sweet, other, agu, rose, fishcake = apply_reference(
            [menu("짬뽕"), menu("볶음짬뽕", soup="no"), menu("탕수육"), menu("마파두부"), menu("아구찜"),
             menu("로제떡볶이"), menu("어묵탕")], TAGS)
        self.assertEqual((jjamppong["soup"], jjamppong["spice"], jjamppong["groups"]), ("yes", "hot", ["중식", "면"]))
        self.assertEqual((fried["soup"], fried["spice"]), ("no", ""))  # 이름으로 정한 값 유지
        self.assertEqual(sweet["soup"], "")  # '애매'는 채우지 않는다
        self.assertNotIn("groups", other)
        self.assertEqual((agu["spice"], agu["groups"]), ("hot", ["중식", "찜·조림", "해산물"]))
        self.assertEqual((rose["spice"], rose["groups"]), ("", ["중식", "분식"]))  # 끝만 같으면 맵기는 안 가져온다
        self.assertEqual(fishcake["soup"], "yes")  # 이름 끝이 탕이면 국물 있음
        self.assertEqual(apply_reference([menu("짬뽕")], None)[0]["soup"], "")

    def test_meat_label_from_reference_and_names(self):
        columns = {"제육볶음": {"groups": "고기", "broth": "없음", "meat_preferred": True},
                   "햄버거": {"groups": "버거·샌드위치", "broth": "없음", "meat_preferred": False},
                   "콩고기구이": {"groups": "샐러드·건강식", "broth": "없음", "meat_preferred": False},
                   "회": {"groups": "해산물", "broth": "없음", "meat_preferred": True}}
        tags = reference_tags(columns, [])
        self.assertEqual((tags["제육볶음"]["meat"], tags["햄버거"]["meat"]), (True, False))
        menus = apply_reference([menu("제육볶음", "고기", menu_id="MN_1"), menu("햄버거", "버거", menu_id="MN_2"),
                                 menu("콩고기구이", "건강식", menu_id="MN_3"), menu("모듬회", "해산물", menu_id="MN_4", food="회"),
                                 menu("탕수육", menu_id="MN_5"), menu("비빔밥", "밥", menu_id="MN_6")], tags)
        reasons = {m["name"]: m["reasons"] for m in recommend(menus, Profile.parse({"diet": "meat"}, []))["items"]}
        # 라벨이 있으면 라벨대로(햄버거는 육식 선호 대상 아님). 라벨이 없으면 이름 단서(MEAT)만 본다.
        self.assertIn("육식 선호 일치", reasons["제육볶음"])
        self.assertIn("육식 선호 일치", reasons["모듬회"])
        self.assertNotIn("육식 선호 일치", reasons["햄버거"])
        # 채식: 충돌 메뉴만 빼고 나머지는 '재료 확인 필요'로 추천한다. 모듬회는 이름 단서가 없어도
        # 참고자료 육식 라벨로 빠지고, 콩고기는 고기로 보지 않는다.
        result = recommend(menus, Profile.parse({"diet": "vegetarian"}, []))
        self.assertEqual({m["name"] for m in result["items"]}, {"콩고기구이", "비빔밥"})
        self.assertEqual(result["excluded"], 4)

    def test_clashing_spice_or_soup_is_dropped(self):
        menus = apply_reference([menu("짬뽕", menu_id="MN_1"), menu("피자", "피자", menu_id="MN_2"),
                                 menu("탕수육", menu_id="MN_3")], TAGS)
        categories = ["면", "중식", "피자"]
        mild = recommend(menus, Profile.parse({"spice": "mild"}, categories))
        self.assertEqual([item["name"] for item in mild["items"]], ["피자", "탕수육"])
        self.assertEqual(mild["mismatched"], 1)
        dry = recommend(menus, Profile.parse({"soup": "no"}, categories))
        self.assertNotIn("짬뽕", [item["name"] for item in dry["items"]])
        self.assertIn("국물 정보 부족", dry["items"][-1]["reasons"])  # 모르는 메뉴는 남긴다

    def test_preference_groups_match_any_reference_group(self):
        menus = apply_reference([menu("짬뽕")], TAGS)
        result = recommend(menus, Profile.parse({"category": "면", "soup": "yes", "spice": "hot"}, ["면", "중식"]))
        self.assertEqual(result["items"][0]["reasons"][3:], ["음식군 일치", "맵기 일치", "국물 일치"])
        chosen = recommend(menus, Profile.parse({"meal_category": "면"}, ["면", "중식"]))
        self.assertEqual(chosen["eligible"], 1)
        plain = recommend([menu("짬뽕")], Profile.parse({"category": "면"}, ["면", "중식"]))
        self.assertEqual(plain["items"][0]["reasons"][3], "음식군 불일치")

    def test_food_level_tag_beats_one_spicy_variant(self):
        menus = apply_reference([menu("매콤한 감자피자", "피자", spice="hot", menu_id="MN_1", food="피자"),
                                 menu("콤비네이션피자", "피자", menu_id="MN_2", food="피자"),
                                 menu("매운오뎅김밥", "분식", spice="hot", menu_id="MN_3", food="김밥"),
                                 menu("참치김밥", "분식", menu_id="MN_4", food="김밥"),
                                 menu("야채김밥", "분식", menu_id="MN_5", food="김밥"),
                                 menu("불족발", "족발·보쌈", spice="hot", menu_id="MN_6", food="불족발"),
                                 menu("매운불족발", "족발·보쌈", spice="hot", menu_id="MN_7", food="불족발")], TAGS)
        self.assertEqual({m["components"][0]: m["family_spice"] for m in menus},
                         {"피자": "mild", "김밥": "", "불족발": "hot"})
        result = recommend(menus, Profile.parse({"spice": "hot"}, ["피자", "분식", "족발·보쌈"]))
        names = {item["name"] for item in result["items"]}
        self.assertFalse(names & {"매콤한 감자피자", "콤비네이션피자"})  # 피자 자체가 안 매운 음식
        self.assertTrue({"불족발", "매운불족발", "매운오뎅김밥", "참치김밥"} <= names)  # 김밥은 미정이라 남는다

    def test_missing_nutrition_gets_median_and_preferred_group_first(self):
        def fed(name, category, menu_id, protein):
            item = menu(name, category, menu_id=menu_id)
            item["nutrition"] = {"basis": 100, "unit": "g", "values": [120, protein, 13, 4, None]}
            return item
        menus = [fed("피자", "피자", "MN_1", 12), fed("김밥", "분식", "MN_2", 3), fed("라면", "분식", "MN_3", 6),
                 menu("제육볶음", "고기", menu_id="MN_4"), menu("삼겹살", "고기", menu_id="MN_5"),
                 menu("곱창볶음", "고기", menu_id="MN_6")]
        categories = ["피자", "분식", "고기"]
        plain = recommend(menus, Profile.parse({"goal": "protein"}, categories))
        self.assertEqual(plain["items"][0]["name"], "피자")
        self.assertEqual(plain["items"][1]["name"], "라면")  # 중앙값과 같으면 영양 자료가 있는 메뉴가 앞
        meat = recommend(menus, Profile.parse({"goal": "protein", "category": "고기"}, categories))
        self.assertEqual([item["category"] for item in meat["items"][:3]], ["고기"] * 3)

    def test_unknown_taste_is_penalized_and_order_follows_score(self):
        rich = menu("육회", "고기", menu_id="MN_1")
        rich["nutrition"] = {"basis": 100, "unit": "g", "values": [130, 20, 1, 5, None]}
        menus = apply_reference([rich, menu("제육볶음", "고기", spice="hot", soup="no", menu_id="MN_2"),
                                 menu("콩불", "고기", spice="hot", soup="no", menu_id="MN_3"),
                                 menu("오돌뼈", "고기", spice="hot", soup="no", menu_id="MN_4")], TAGS)
        result = recommend(menus, Profile.parse({"spice": "hot", "soup": "no", "category": "고기", "goal": "protein"},
                                                ["고기"]))
        self.assertEqual(result["items"][-1]["name"], "육회")  # 영양은 좋아도 맵기·국물을 확인할 수 없다
        scores = [item["score"] for item in result["items"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_side_food_only_by_search_and_dry_names(self):
        menus = apply_reference([menu("고기만두", "분식", menu_id="MN_1"), menu("라면", "분식", menu_id="MN_2"),
                                 menu("삼겹살", "고기", menu_id="MN_3")], TAGS)
        self.assertEqual(menus[2]["soup"], "no")  # 이름 끝이 살이면 국물 없음
        plain = recommend(menus, Profile.parse({}, ["분식", "고기"]))
        self.assertNotIn("고기만두", [item["name"] for item in plain["items"]])
        search = recommend(menus, Profile.parse({"query": "만두"}, ["분식", "고기"]))
        self.assertEqual([item["name"] for item in search["items"]], ["고기만두"])

    def test_penalty_reasons_and_preferred_count(self):
        menus = apply_reference([menu("김치전", "전·부침", menu_id="MN_1"), menu("짬뽕", menu_id="MN_2")], TAGS)
        result = recommend(menus, Profile.parse({"category": "전·부침", "spice": "hot"}, ["전·부침", "중식"]))
        self.assertEqual((result["category"], result["preferred_foods"]), ("전·부침", 1))
        self.assertEqual(result["items"][0]["name"], "김치전")  # 선호 음식군 음식이 하나여도 앞에
        jjamppong = result["items"][1]
        self.assertIn("선호 음식군 밖이라 25점 감점", jjamppong["reasons"])
        self.assertIn("고른 맵기·국물을 확인할 수 없어 15점 감점", result["items"][0]["reasons"])
        self.assertTrue(jjamppong["reasons"][1].startswith("참고 영양값 없음 · 후보 중앙값"))
        none = recommend(menus, Profile.parse({"category": "피자"}, ["전·부침", "중식", "피자"]))
        self.assertEqual(none["preferred_foods"], 0)

    def test_reference_data_backs_off_after_failure(self):
        with patch.dict(menu_reco._failed, {"at": float("-inf")}), \
                patch.object(menu_reco, "data", side_effect=OSError("down")) as load:
            self.assertIsNone(menu_reco.reference_data())
            self.assertIsNone(menu_reco.reference_data())
            self.assertEqual(load.call_count, 1)


if __name__ == "__main__":
    unittest.main()
