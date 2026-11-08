import json
import os
import sqlite3
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from drive_data import EXPECTED
from prepare import CATALOG, reference, taste_tags
from raw import LOCAL, ordered_menus
from recommend import Profile, ingredient_terms, nutrition_score, recommend
from server import catalog, categories, public_menu


def fixture(name, menu_id="MN_1", nutrition=None):
    return {"name": name, "standard_menu_id": menu_id, "category": "밥·덮밥",
            "components": [name], "spice": "", "soup": "no", "nutrition": nutrition,
            "regions": {"수원시": {"reviews": 3, "stores": 1}}}


class RulesTest(unittest.TestCase):
    def test_parse_and_aliases(self):
        profile = Profile.parse({"diet": "vegan", "goal": "protein"}, [])
        self.assertEqual((profile.diet, profile.goal), ("vegan", "protein"))
        self.assertIn("달걀", ingredient_terms("계란, 오이"))
        for value in ([], {"district": "서울"}, {"query": "가" * 41}, {"exclude": ",,,"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Profile.parse(value, [])

    def test_strict_restrictions_are_never_relaxed(self):
        menus = [fixture("계란김밥", "MN_1"), fixture("두부덮밥", "MN_2"),
                 fixture("돼지불고기덮밥", "MN_3")]
        result = recommend(menus, Profile.parse({"diet": "vegan", "goal": "protein"}, []))
        self.assertEqual((result["eligible"], result["excluded"], result["unverified"]), (0, 2, 1))
        self.assertEqual(result["check_only"][0]["name"], "두부덮밥")
        for field in ("allergy", "exclude"):
            result = recommend(menus, Profile.parse({field: "달걀"}, []))
            self.assertEqual((result["eligible"], result["excluded"], result["unverified"]), (0, 1, 2))

    def test_nutrition_missing_units_and_scoring(self):
        info = {"basis": 100, "unit": "g", "values": [120, 6, 13, 4, None]}
        self.assertEqual(nutrition_score(info, "protein"), 0.5)
        self.assertIsNone(nutrition_score({**info, "unit": "mL"}, "protein"))
        self.assertIsNone(nutrition_score({**info, "values": [120, None, 13, 4, 10]}, "balance"))
        self.assertIsNone(nutrition_score(None, "light"))
        menus = [fixture("영양자료 없음", "MN_1"), fixture("두부덮밥", "MN_2", info)]
        result = recommend(menus, Profile.parse({"goal": "protein"}, []))
        self.assertEqual(result["items"][0]["standard_menu_id"], "MN_2")
        self.assertIsNone(result["items"][1]["nutrition"])

    def test_query_short_result_and_tie(self):
        menus = [fixture("김밥", "MN_2"), fixture("비빔밥", "MN_1")]
        profile = Profile.parse({}, [])
        self.assertEqual(recommend(menus, profile), recommend(list(reversed(menus)), profile))
        self.assertEqual(recommend(menus, profile)["items"][0]["standard_menu_id"], "MN_1")
        self.assertEqual(recommend(menus, Profile.parse({"query": "비빔밥"}, []))["eligible"], 1)
        self.assertEqual(recommend(menus, Profile.parse({"district": "권선구"}, []))["eligible"], 0)

    def test_json_and_heuristic_tags(self):
        self.assertEqual(ordered_menus('[" 닭볶음탕 ", "닭볶음탕", "김밥+라면"]'), ["김밥+라면", "닭볶음탕"])
        for value in ('{"menu":"김밥"}', '["김밥",5]', "["):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ordered_menus(value)
        self.assertEqual(taste_tags("안매운 우동", [], "면"), ("mild", "yes"))
        self.assertEqual(taste_tags("볶음우동", [], "면"), ("", "no"))
        self.assertIsNone(reference({}, 1, {}, {}))

    def test_db_unavailable(self):
        with patch("server.sqlite3.connect", side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.OperationalError):
                catalog()


AUDIT_DIR = Path(os.environ.get("PICKMENU_AUDIT_DIR") or LOCAL).resolve()
TEST_CATALOG = Path(os.environ.get("PICKMENU_CATALOG_PATH") or CATALOG).resolve()


@unittest.skipUnless(TEST_CATALOG.exists(), "전처리 완료 후 통합 검증 실행")
class ActualDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.menus = catalog()
        cls.categories = categories(cls.menus)

    def test_full_audit(self):
        raw = json.loads((AUDIT_DIR / "raw-audit.json").read_text(encoding="utf-8"))
        self.assertEqual(len(raw), 5)
        total = Counter()
        for entry in raw:
            counts = Counter(entry["counts"])
            total.update(counts)
            self.assertEqual(counts["input"], counts["business_only"] + counts["column_error"]
                             + counts["suwon"] + counts["outside_or_unknown"])
            self.assertEqual(len(entry["archive_matches"]), 1)
        drive = json.loads((AUDIT_DIR / "drive-audit.json").read_text(encoding="utf-8"))
        self.assertTrue(set(EXPECTED).issubset({file["file"] for file in drive}))
        for file in drive:
            for table in file["tables"]:
                counts = Counter(table["counts"])
                self.assertEqual(counts["input"], counts["processed"] + counts["blank"]
                                 + counts["column_error"])
        self.assertEqual(total["suwon_target_kst"], 121150)

    def test_real_rank_and_trace(self):
        profile = Profile.parse({}, self.categories)
        result = recommend(self.menus, profile)
        self.assertEqual(result, recommend(self.menus, profile))
        self.assertEqual(len(result["items"]), 5)
        for item in result["items"]:
            self.assertTrue(item["nutrition"])
            self.assertNotIn("mapping_note", public_menu(item))
            with closing(sqlite3.connect(TEST_CATALOG)) as db:
                count, raw_links = db.execute("""
                    SELECT count(*),sum(raw_link)
                    FROM trace JOIN review USING(review_id) WHERE menu_id=?
                """, (item["standard_menu_id"],)).fetchone()
            self.assertEqual(item["evidence"]["reviews"], count)
            self.assertEqual(count, raw_links)

    def test_actual_nutrition_and_compounds(self):
        self.assertTrue(any(item["nutrition"] is None for item in self.menus))
        self.assertTrue(any(item["compound"] and item["references"] for item in self.menus))
        for item in self.menus:
            if item["compound"]:
                self.assertIsNone(item["nutrition"])
            for ref in item["references"]:
                self.assertEqual((ref["basis"], ref["unit"]), (100, "g"))
                self.assertTrue(ref["food_id"])

    def test_no_sensitive_fields_in_catalog(self):
        forbidden = {"reviewer", "content", "business_number", "shop_address", "original_name"}
        for item in self.menus:
            self.assertFalse(forbidden.intersection(public_menu(item)))


if __name__ == "__main__":
    unittest.main()
