import unittest
from unittest.mock import patch

import menu_reco
from recommend import Profile, apply_reference, recommend


def menu(name, category="중식", spice="", soup="", menu_id="MN_1"):
    return {"name": name, "standard_menu_id": menu_id, "category": category, "components": [name],
            "spice": spice, "soup": soup, "nutrition": None,
            "regions": {"수원시": {"reviews": 30, "stores": 3}}}


COLUMNS = {"짬뽕": {"groups": "중식;면", "broth": "있음", "spiciness": "보통 매움"},
           "볶음짬뽕": {"groups": "중식", "broth": "있음", "spiciness": "-"},
           "탕수육": {"groups": "중식", "broth": "애매", "spiciness": "-"}}


class ReferenceTagTest(unittest.TestCase):
    def test_fills_only_blank_tags_from_reference(self):
        jjamppong, fried, sweet, other = apply_reference(
            [menu("짬뽕"), menu("볶음짬뽕", soup="no"), menu("탕수육"), menu("마파두부")], COLUMNS)
        self.assertEqual((jjamppong["soup"], jjamppong["spice"], jjamppong["groups"]), ("yes", "hot", ["중식", "면"]))
        self.assertEqual((fried["soup"], fried["spice"]), ("no", ""))  # 이름으로 정한 값 유지, '-'는 정보 없음
        self.assertEqual(sweet["soup"], "")  # '애매'는 채우지 않는다
        self.assertNotIn("groups", other)
        self.assertEqual(apply_reference([menu("짬뽕")], None)[0]["soup"], "")

    def test_preference_groups_match_any_reference_group(self):
        menus = apply_reference([menu("짬뽕")], COLUMNS)
        result = recommend(menus, Profile.parse({"category": "면", "soup": "yes", "spice": "hot"}, ["면", "중식"]))
        self.assertEqual(result["items"][0]["reasons"][2:], ["음식군 일치", "맵기 일치", "국물 일치"])
        chosen = recommend(menus, Profile.parse({"meal_category": "면"}, ["면", "중식"]))
        self.assertEqual(chosen["eligible"], 1)
        plain = recommend([menu("짬뽕")], Profile.parse({"category": "면"}, ["면", "중식"]))
        self.assertEqual(plain["items"][0]["reasons"][2], "음식군 불일치")

    def test_food_columns_backs_off_after_failure(self):
        with patch.dict(menu_reco._failed, {"at": float("-inf")}), \
                patch.object(menu_reco, "data", side_effect=OSError("down")) as load:
            self.assertIsNone(menu_reco.food_columns())
            self.assertIsNone(menu_reco.food_columns())
            self.assertEqual(load.call_count, 1)


if __name__ == "__main__":
    unittest.main()
