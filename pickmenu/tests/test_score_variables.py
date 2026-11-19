import unittest

import server
from recommend import (
    RECENT_PENALTY,
    Profile,
    apply_reference,
    column_quality,
    recommend,
    reference_tags,
)


def menu(name, menu_id):
    return {"name": name, "standard_menu_id": menu_id, "category": "중식", "components": [name],
            "spice": "", "soup": "", "nutrition": None, "regions": {"수원시": {"reviews": 40, "stores": 4},
                                                                     "권선구": {"reviews": 40, "stores": 4}}}


COLUMNS = {"짬뽕": {"groups": "중식"}, "짜장면": {"groups": "중식"}}
ROWS = ([{"column_name": "짬뽕", "district": "권선구", "rating": 4.8, "repeat_ratio": 0.6, "review_count": 20}] * 2
        + [{"column_name": "짜장면", "district": "권선구", "rating": 4.4, "repeat_ratio": 0.3, "review_count": 40}])


class ScoreVariableTest(unittest.TestCase):
    def profile(self, district=""):
        return Profile.parse({"district": district}, ["중식"])

    def test_column_quality_weights_by_reviews_and_keeps_small_districts_out(self):
        quality = column_quality(ROWS)
        self.assertAlmostEqual(quality["짬뽕"][""]["rating"], 4.8)
        self.assertEqual(quality["짬뽕"][""]["reviews"], 40)
        self.assertIn("권선구", quality["짬뽕"])  # 40건 ≥ 30건
        small = column_quality([{"column_name": "짬뽕", "district": "권선구", "rating": 4.0, "review_count": 5}])
        self.assertNotIn("권선구", small["짬뽕"])

    def test_satisfaction_and_reorder_raise_score(self):
        menus = apply_reference([menu("짜장면", "MN_1"), menu("짬뽕", "MN_2")], reference_tags(COLUMNS, ROWS))
        result = recommend(menus, self.profile())
        self.assertEqual([f["name"] for f in result["families"]], ["짬뽕", "짜장면"])
        self.assertIn("리뷰 만족도 4.80점 · 단골 주문 60%", result["families"][0]["best"]["reasons"])

    def test_recent_foods_move_down_with_reason(self):
        menus = apply_reference([menu("짜장면", "MN_1"), menu("짬뽕", "MN_2")], reference_tags(COLUMNS, ROWS))
        before = recommend(menus, self.profile())
        after = recommend(menus, self.profile(), ["짬뽕", "짬뽕", "짬뽕"])
        top = {f["name"]: f["best"]["score"] for f in before["families"]}
        moved = {f["name"]: f["best"]["score"] for f in after["families"]}
        self.assertAlmostEqual(top["짬뽕"] - moved["짬뽕"], RECENT_PENALTY * 2, places=2)  # 최대 2번만 깎는다
        self.assertEqual(after["recent_applied"], 1)
        self.assertTrue(any("최근 추천에 2번" in r for r in after["families"][-1]["best"]["reasons"]
                            + after["families"][0]["best"]["reasons"]))

    def test_recent_payload_is_validated(self):
        self.assertEqual(server.recent_foods({"recent": ["짬뽕"]}), ["짬뽕"])
        self.assertEqual(server.recent_foods({}), [])
        for bad in ("짬뽕", ["x"] * 31, [1], [""], ["가" * 41]):
            with self.assertRaises(ValueError):
                server.recent_foods({"recent": bad})


if __name__ == "__main__":
    unittest.main()
