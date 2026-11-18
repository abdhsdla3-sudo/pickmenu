import unittest
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


COLUMNS = {"짬뽕": {"groups": "중식;면", "broth": "있음", "spiciness": "보통 매움"},
           "볶음짬뽕": {"groups": "중식", "broth": "있음", "spiciness": "-"},
           "탕수육": {"groups": "중식", "broth": "애매", "spiciness": "-"},
           "피자": {"groups": "피자", "broth": "없음", "spiciness": "-"},
           "떡볶이": {"groups": "분식", "broth": "애매", "spiciness": "보통 매움"},
           "아구찜·해물찜": {"groups": "찜·조림;해산물", "broth": "없음", "spiciness": "보통 매움"},
           "어묵": {"groups": "분식", "broth": "없음", "spiciness": "-"}}
ROWS = ([{"column_name": "피자", "spiciness": "안 매움"}] * 9 + [{"column_name": "피자", "spiciness": "약간 매움"}]
        + [{"column_name": "탕수육", "spiciness": "안 매움"}, {"column_name": "탕수육", "spiciness": "일부 매운 옵션"}]
        + [{"column_name": "짬뽕", "spiciness": "매움"}, {"column_name": "짬뽕", "spiciness": "명시 없음"}])
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
        self.assertEqual(result["items"][0]["reasons"][2:], ["음식군 일치", "맵기 일치", "국물 일치"])
        chosen = recommend(menus, Profile.parse({"meal_category": "면"}, ["면", "중식"]))
        self.assertEqual(chosen["eligible"], 1)
        plain = recommend([menu("짬뽕")], Profile.parse({"category": "면"}, ["면", "중식"]))
        self.assertEqual(plain["items"][0]["reasons"][2], "음식군 불일치")

    def test_reference_data_backs_off_after_failure(self):
        with patch.dict(menu_reco._failed, {"at": float("-inf")}), \
                patch.object(menu_reco, "data", side_effect=OSError("down")) as load:
            self.assertIsNone(menu_reco.reference_data())
            self.assertIsNone(menu_reco.reference_data())
            self.assertEqual(load.call_count, 1)


if __name__ == "__main__":
    unittest.main()
