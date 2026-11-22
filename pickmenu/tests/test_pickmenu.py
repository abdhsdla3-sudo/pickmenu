import json
import os
import sqlite3
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from drive_data import EXPECTED, RECIPE, inspect_table
from prepare import CATALOG, recipe_cooking_methods, reference, taste_tags
from raw import LOCAL, ordered_menus
from recommend import (
    Profile,
    cooking_signal,
    ingredient_terms,
    nutrition_score,
    recommend,
    recommend_relaxed,
)
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

    def test_restrictions_drop_conflicts_and_flag_unknowns(self):
        menus = [fixture("계란김밥", "MN_1"), fixture("두부덮밥", "MN_2"),
                 fixture("돼지불고기덮밥", "MN_3")]
        # 이름·구성에서 충돌이 보이면 빼고, 알 수 없는 메뉴는 '재료 확인 필요'로 추천한다.
        result = recommend(menus, Profile.parse({"diet": "vegan", "goal": "protein"}, []))
        self.assertEqual((result["eligible"], result["excluded"], result["unverified"]), (1, 2, 1))
        self.assertEqual(result["check_only"][0]["name"], "두부덮밥")
        self.assertTrue(result["items"][0]["needs_check"])
        self.assertIn("비건", result["items"][0]["reasons"][-1])
        result = recommend(menus, Profile.parse({"allergy": "달걀"}, []))
        self.assertEqual((result["eligible"], result["excluded"], result["unverified"]), (2, 1, 2))
        self.assertTrue(all(item["needs_check"] for item in result["items"]))
        self.assertNotIn("계란김밥", [item["name"] for item in result["items"]])
        # 채식이면 고기·해산물 육수가 보통 들어가는 음식(국밥·짬뽕 등)도 뺀다.
        result = recommend([fixture("순대국밥", "MN_4"), fixture("짬뽕", "MN_5"), fixture("비빔밥", "MN_6")],
                           Profile.parse({"diet": "vegetarian"}, []))
        self.assertEqual([item["name"] for item in result["items"]], ["비빔밥"])
        # 밀 알레르기면 면·피자처럼 보통 밀이 들어가는 음식도 뺀다.
        result = recommend([fixture("콩국수", "MN_7"), fixture("피자", "MN_8"), fixture("비빔밥", "MN_6")],
                           Profile.parse({"allergy": "밀"}, []))
        self.assertEqual([item["name"] for item in result["items"]], ["비빔밥"])
        # 싫어하는 재료는 안전 문제가 아니라 확인 표시 없이 추천한다.
        result = recommend(menus, Profile.parse({"exclude": "달걀"}, []))
        self.assertEqual((result["eligible"], result["excluded"], result["unverified"]), (2, 1, 0))
        self.assertFalse(any(item["needs_check"] for item in result["items"]))

    def test_taste_preference(self):
        menus = [fixture("물냉면", "MN_1"), fixture("돈가스", "MN_2")]
        self.assertEqual(recommend(menus, Profile.parse({"taste": "sour"}, []))["items"][0]["name"], "물냉면")
        self.assertEqual(recommend(menus, Profile.parse({"taste": "crispy"}, []))["items"][0]["name"], "돈가스")
        self.assertEqual(recommend(menus, Profile.parse({"taste": "-crispy"}, []))["items"][0]["name"], "물냉면")
        with self.assertRaises(ValueError):
            Profile.parse({"taste": "umami"}, [])

    def test_exact_name_search_first(self):
        info = {"basis": 100, "unit": "g", "values": [120, 20, 13, 4, None]}
        menus = [fixture("치킨마요덮밥", "MN_1", info), fixture("양념치킨", "MN_2"), fixture("치킨", "MN_3")]
        names = [item["name"] for item in recommend(menus, Profile.parse({"query": "치킨", "goal": "protein"}, []))["items"]]
        self.assertEqual(names, ["치킨", "양념치킨", "치킨마요덮밥"])

    def test_less_greasy_uses_comparable_fat_values(self):
        lean = {"basis": 100, "unit": "g", "values": [140, 8, 16, 3, 250]}
        rich = {**lean, "values": [140, 8, 16, 14, 250]}
        menus = [fixture("돼지고기덮밥", "MN_1", rich), fixture("채소덮밥", "MN_2", lean),
                 fixture("버섯덮밥", "MN_3", {**lean, "basis": 200})]
        result = recommend(menus, Profile.parse({"goal": "light", "taste": "-rich"}, []))
        self.assertEqual([item["name"] for item in result["items"]], ["채소덮밥", "버섯덮밥", "돼지고기덮밥"])
        self.assertIn("지방 100g당 3g (일반음식 참고값)", result["items"][0]["reasons"])
        self.assertIn("지방 참고값 없음 · 메뉴 이름 단서만 참고", result["items"][1]["reasons"])

    def test_cooking_is_secondary_and_its_source_is_shown(self):
        lean = {"basis": 100, "unit": "g", "values": [140, 8, 16, 2, 250]}
        rich = {**lean, "values": [140, 8, 16, 16, 250], "serving_reference": "1인분 (100g)"}
        steam = {**fixture("두부 요리", "MN_1", rich), "cooking_method": "찌기"}
        fry = fixture("두부튀김", "MN_2", lean)
        profile = Profile.parse({"goal": "light", "taste": "-rich"}, [])
        result = recommend([steam, fry], profile)
        self.assertEqual(result["items"][0]["name"], "두부튀김")
        self.assertTrue(any("튀기기 (대표 메뉴명 추정)" in note for note in result["items"][0]["reasons"]))
        self.assertTrue(any("찌기 (공개 참고 레시피)" in note for note in result["items"][1]["reasons"]))
        self.assertTrue(any("1인분 정보 없음" in note for note in result["items"][0]["reasons"]))
        self.assertTrue(any("1인분 일반음식 참고값" in note for note in result["items"][1]["reasons"]))
        self.assertEqual(cooking_signal(fixture("아구찜")), ("", ""))
        self.assertEqual(cooking_signal({**steam, "compound": True}), ("", ""))
        menus = [{**steam, "nutrition": None}, {**fry, "nutrition": None}, fixture("두부구이", "MN_3")]
        result = recommend(menus, profile)
        self.assertEqual([item["name"] for item in result["items"]], ["두부 요리", "두부구이", "두부튀김"])

    def test_only_unambiguous_reviewed_recipe_links_are_used(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE records(file, sheet, row_number, data)")
            inspect_table(db, RECIPE, "레시피자료", [
                ["레시피ID", "조리방법"], ["1", "찌기"], ["2", "튀기기"], ["3", "기타"],
            ])
            approved = "AI본문검토·유형참고연결"
            inspect_table(db, RECIPE, "메뉴레시피검색", [
                ["표준메뉴ID", "참고레시피ID", "레시피연결승인여부", "참고연결검토상태"],
                ["MN_1", "1", 1, approved], ["MN_2", "2", 0, approved],
                ["MN_3", "1", 1, approved], ["MN_3", "2", 1, approved], ["MN_4", "3", 1, approved],
            ])
            self.assertEqual(recipe_cooking_methods(db), {"MN_1": "찌기"})

    def test_saved_group_first_and_options_removed(self):
        menus = [fixture("육회", "MN_1"), {**fixture("샐러드", "MN_2"), "category": "샐러드·건강식"},
                 fixture("곱빼기 추가", "MN_3")]
        profile = Profile.parse({"category": "샐러드·건강식", "goal": "light"}, ["샐러드·건강식"])
        found = recommend(menus, profile, ["샐러드", "샐러드"])
        self.assertEqual([row["name"] for row in found["families"]], ["샐러드", "육회"])
        self.assertEqual(recommend(menus, Profile.parse({"avoid_menus": "육회"}, []))["eligible"], 1)

    def test_conversation_keeps_soup_filter_and_orders_by_score(self):
        rich = {"basis": 100, "unit": "g", "values": [500, 0, 0, 55, 250]}
        balanced = {**rich, "values": [500, 27.5, 66.25, 13.89, 250]}
        bulgogi = {**fixture("소불고기", "MN_1"), "category": "고기", "soup": "", "family_soup": ""}
        stew = {**fixture("고기전골", "MN_2", rich), "category": "고기", "soup": "yes"}
        noodle = {**fixture("짬뽕", "MN_3", balanced), "category": "중식", "soup": "yes", "family_soup": "",
                  "regions": {"수원시": {"reviews": 100, "stores": 5}}}
        menus = [bulgogi, stew, noodle, fixture("비빔밥", "MN_4")]
        profile = Profile.parse({"category": "고기", "soup": "yes"}, ["고기", "중식"])
        result, used = recommend_relaxed(menus, profile, conversation=True)
        self.assertEqual([f["name"] for f in result["families"]], ["짬뽕", "고기전골"])
        self.assertGreater(result["families"][0]["best"]["score"], result["families"][1]["best"]["score"])
        self.assertIn("국물 일치", result["families"][0]["best"]["reasons"])
        self.assertEqual((result["relaxed"], used.soup), ([], "yes"))
        result, used = recommend_relaxed([bulgogi], profile, conversation=True)
        self.assertEqual((result["families"], result["relaxed"], used.soup), ([], [], "yes"))
        # 대표 메뉴 이름이 아닌 검색어만으로 0개가 되면 이야기 추천에서는 검색어를 뺀다.
        result, used = recommend_relaxed(menus, Profile.parse({"query": "치팅"}, []), conversation=True)
        self.assertEqual((result["relaxed"], used.query), (["검색어(치팅)"], ""))
        self.assertTrue(result["families"])
        result, _ = recommend_relaxed(menus, Profile.parse({"query": "치팅"}, []))
        self.assertEqual(result["families"], [])

    def test_excluded_categories_and_groups(self):
        noodle = {**fixture("짬뽕", "MN_1"), "category": "중식", "groups": ["중식", "면"]}
        fried = {**fixture("깐풍기", "MN_2"), "category": "중식"}
        rice = {**fixture("비빔밥", "MN_3"), "category": "밥·덮밥"}
        menus = [noodle, fried, rice]
        names = lambda profile: [f["name"] for f in recommend(menus, profile)["families"]]  # noqa: E731
        cats = ["중식", "면", "밥·덮밥"]
        self.assertEqual(names(Profile.parse({"exclude_categories": "면"}, cats)), ["깐풍기", "비빔밥"])
        self.assertEqual(names(Profile.parse({"exclude_categories": "중식"}, cats)), ["비빔밥"])
        self.assertEqual(sorted(names(Profile.parse({"exclude_groups": "튀김"}, cats))), ["비빔밥", "짬뽕"])
        self.assertEqual(names(Profile.parse({"exclude_groups": "밀가루"}, cats)), ["깐풍기", "비빔밥"])
        for bad in ({"exclude_categories": "없는음식군"}, {"exclude_groups": "아무거나"}, {"exclude_groups": 3}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Profile.parse(bad, cats)

    def test_mild_excludes_inferred_hot_without_relaxing(self):
        menus = [fixture(name, str(i)) for i, name in enumerate(("불족발", "고추바사삭", "부대찌개"))]
        result, used = recommend_relaxed(menus, Profile.parse({"spice": "mild"}, []))
        self.assertEqual((used.spice, result["items"]), ("mild", []))
        self.assertEqual(recommend(menus, Profile.parse({"spice": ""}, []))["eligible"], 3)

    def test_selected_spice_requires_evidence_even_with_preferred_group(self):
        plain = {**fixture("소불고기", "MN_1"), "category": "고기"}
        known_mild = {**fixture("오리고기", "MN_2"), "category": "고기", "spice": "mild"}
        variant = {**fixture("매운 삼겹살", "MN_3"), "category": "고기", "spice": "hot",
                   "components": ["삼겹살"], "family_spice": ""}
        grouped = {**fixture("제육볶음", "MN_4"), "category": "고기", "family_spice": "hot"}
        conflict = {**fixture("안 매운 제육", "MN_5"), "category": "고기", "spice": "mild",
                    "family_spice": "hot"}
        in_request = {"category": "고기", "goal": "protein"}
        for spice, expected in (("hot", {"매운 삼겹살", "제육볶음"}), ("mild", {"오리고기"})):
            profile = Profile.parse({**in_request, "spice": spice}, ["고기"])
            with self.subTest(spice=spice):
                result, actual = recommend_relaxed([plain, known_mild, variant, grouped, conflict], profile)
                self.assertEqual({f["best"]["name"] for f in result["families"]}, expected)
                self.assertTrue(all("맵기 일치" in f["best"]["reasons"] for f in result["families"]))
                self.assertEqual((actual.spice, result["relaxed"]), (spice, []))
                empty, used = recommend_relaxed([plain], profile)
                self.assertEqual((used.spice, empty["families"], empty["relaxed"]), (spice, [], []))

    def test_light_meal_checks_sodium_and_serving(self):
        stew = {"basis": 100, "unit": "g", "values": [61, 3.8, 3, 3.8, 491]}
        lighter = {**stew, "values": [61, 3.8, 3, 3.8, 40]}
        serving = {**stew, "serving_reference": "1인분(400g)"}
        self.assertLess(nutrition_score(stew, "light"), nutrition_score(lighter, "light"))
        self.assertLess(nutrition_score(serving, "light"), nutrition_score(stew, "light"))

    def test_relax_impossible_combination(self):
        menus = [fixture("비빔밥", "MN_1")]  # 국물 없는 밥·덮밥
        result, used = recommend_relaxed(menus, Profile.parse({"soup": "yes"}, []))
        self.assertEqual((result["relaxed"], used.soup, result["items"][0]["name"]), (["국물 조건"], "", "비빔밥"))
        result, used = recommend_relaxed(menus, Profile.parse({"soup": "no"}, []))
        self.assertEqual(result["relaxed"], [])
        result, _ = recommend_relaxed(menus, Profile.parse({"diet": "vegan", "query": "돼지"}, []))
        self.assertEqual((result["relaxed"], result["items"]), ([], []))
        # 고른 지역에 말한 메뉴가 없으면(이름만 비슷한 닭갈비덮밥만 있으면) 지역만 넓힌다. 국물 조건은 남긴다.
        near = {"권선구": {"reviews": 3, "stores": 1}, "수원시": {"reviews": 3, "stores": 1}}
        far = {"영통구": {"reviews": 9, "stores": 2}, "수원시": {"reviews": 9, "stores": 2}}
        menus = [{**fixture("닭갈비덮밥", "MN_1"), "regions": near}, {**fixture("닭갈비", "MN_2"), "regions": far}]
        result, used = recommend_relaxed(menus, Profile.parse({"query": "닭갈비", "district": "권선구", "soup": "no"}, []))
        self.assertEqual((result["relaxed"], used.soup, result["items"][0]["name"]),
                         (["지역(수원시 전체로 넓힘)"], "no", "닭갈비"))

    def test_near_column_spice(self):
        from recommend import column_spice, near_spice
        self.assertEqual(near_spice("치즈버거", "햄버거", "mild"), "mild")
        self.assertEqual(near_spice("김치볶음밥", "볶음밥", "mild"), "")
        self.assertEqual(near_spice("로제떡볶이", "떡볶이", "hot"), "")
        self.assertEqual(near_spice("국물떡볶이", "떡볶이", "hot"), "hot")
        self.assertEqual(near_spice("계란덮밥", "덮밥", "hot"), "")
        rows = ([{"column_name": "햄버거", "spiciness": "안 매움"}] * 20 + [{"column_name": "햄버거", "spiciness":
                "일부 매운 옵션"}] * 10 + [{"column_name": "햄버거", "spiciness": "약간 매움"}] * 2)
        self.assertEqual(column_spice(rows), {"햄버거": "mild"})

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

    def test_meal_category_is_an_exact_filter(self):
        menus = [fixture("돼지고기덮밥", "MN_1"),
                 {**fixture("갈비", "MN_2"), "category": "고기"}]
        profile = Profile.parse({"category": "밥·덮밥", "meal_category": "고기"}, ["밥·덮밥", "고기"])
        result = recommend(menus, profile)
        self.assertEqual([m['standard_menu_id'] for m in result['items']], ['MN_2'])
        self.assertEqual(recommend(menus, Profile.parse({'meal_category': '고기', 'district': '권선구'}, ['고기']))['eligible'], 0)
        with self.assertRaises(ValueError):
            Profile.parse({'meal_category': '없는 음식군'}, ['고기'])

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
