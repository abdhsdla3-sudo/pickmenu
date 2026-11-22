import importlib.util
import json
import unittest
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1] / "eval"
spec = importlib.util.spec_from_file_location("story_regression", EVAL / "story_regression.py")
assert spec and spec.loader
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)


class StoryRegressionTest(unittest.TestCase):
    foods = {"치킨": {"치킨"}, "죽": {"죽"}}

    def case(self, mode, expect, **extra):
        return {"mode": mode, "expect": expect, "profile": {"soup": "yes"}, **extra}

    def test_strict_keeps_saved_profile_and_lists_mean_any(self):
        case = self.case("strict", {"spice": ["", "mild"]})
        self.assertEqual(story.judge(case, {"soup": "yes"}, ["치킨"], self.foods), [])
        self.assertEqual(story.judge(case, {"soup": "yes", "spice": "mild"}, ["치킨"], self.foods), [])
        self.assertTrue(story.judge(case, {"soup": "yes", "spice": "hot"}, ["치킨"], self.foods))
        self.assertTrue(story.judge(case, {}, ["치킨"], self.foods))  # 저장한 국물 조건을 잃음

    def test_listed_empty_value_means_released(self):
        case = self.case("listed", {"soup": ""})
        self.assertEqual(story.judge(case, {"spice": "hot"}, ["치킨"], self.foods), [])
        self.assertTrue(story.judge(case, {"soup": "yes"}, ["치킨"], self.foods))

    def test_has_and_menu_checks(self):
        case = self.case("listed", {"avoid_menus_has": ["치킨", ""]}, not_families=["피자"], block_terms=["새우"])
        self.assertEqual(story.judge(case, {"avoid_menus": "치킨,피자"}, ["죽"], self.foods), [])
        wrong = story.judge(case, {}, ["피자", "새우튀김"], self.foods)
        self.assertEqual(len(wrong), 3)
        self.assertTrue(story.judge(self.case("listed", {}, no_results=True), {}, ["죽"], self.foods))
        self.assertTrue(story.judge(self.case("listed", {}, nonempty=True), {}, [], self.foods))

    def test_same_result_counts_as_right(self):
        # 메뉴를 기대했는데 같은 이름의 음식군으로 받거나 그 메뉴가 1위면 맞음
        case = self.case("listed", {"query": "죽"})
        self.assertEqual(story.judge(case, {"meal_category": "죽"}, ["전복죽"], self.foods), [])
        self.assertEqual(story.judge(case, {}, ["죽"], self.foods), [])

    def test_regression_file_is_well_formed(self):
        with open(EVAL / "story_regression.jsonl", encoding="utf-8") as f:
            cases = [json.loads(line) for line in f if line.strip()]
        self.assertGreaterEqual(len(cases), 2000)
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        for case in cases:
            self.assertIn(case["mode"], ("strict", "listed"))
            self.assertTrue(case["messages"] and case["ai_runs"], case["id"])
