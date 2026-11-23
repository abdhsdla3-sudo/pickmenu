import csv
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import nutrition_links

HEADER = ["식품코드", "식품명", "식품기원명", "영양성분함량기준량", "에너지(kcal)", "단백질(g)", "탄수화물(g)",
          "지방(g)", "나트륨(mg)", "출처명", "업체명"]
ROWS = [
    ["D301-1", "쌀밥", "외식(분석함량)", "100g", "166", "3.36", "37.33", "0.32", "0", "식품의약품안전처", "해당없음"],
    ["D406-1", "고추장찌개_두부", "외식(재료량 기반 산출함량)", "100ml", "56", "", "", "", "300", "식품의약품안전처",
     "해당없음"],
    ["D508-1", "돼지불고기_간장", "초등학교급식(재료량 기반 산출 함량)", "100ml", "84", "5", "6", "4", "200",
     "식품의약품안전처", "해당없음"],
    ["D212-1", "닭튀김_New치킨너겟", "외식(프랜차이즈 등 업체 제공 영양정보)", "100g", "237", "16.24", "", "", "430",
     "식품의약품안전처", "롯데리아"],
    ["P117-1", "LA갈비", "가공식품", "100g", "158", "15", "10", "7", "355", "식품의약품안전처", "주원푸드"],
]


def menu(mid: str, name: str, component: str, nutrition=None) -> dict:
    return {"standard_menu_id": mid, "name": name, "category": "밥·덮밥", "components": [component],
            "compound": False, "nutrition": nutrition, "references": [], "nutrition_status": "자료 없음"}


class NutritionLinkTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.catalog = self.dir / "catalog.sqlite3"
        scored = {"name": "기존", "food_id": "X", "basis": 100, "unit": "g", "values": [1, 2, 3, 4, 5]}
        with closing(sqlite3.connect(self.catalog)) as db:
            db.execute("CREATE TABLE catalog(id TEXT PRIMARY KEY, data TEXT)")
            for item in (menu("M1", "공기밥", "공깃밥"), menu("M2", "치킨너겟", "치킨너겟"),
                         menu("M3", "LA갈비구이", "LA갈비"), menu("M4", "허니버터치킨", "허니버터치킨"),
                         menu("M5", "갈비탕", "갈비탕", scored), menu("M6", "고추장찌개", "고추장찌개")):
                db.execute("INSERT INTO catalog VALUES(?,?)", (item["standard_menu_id"], json.dumps(item)))
            db.commit()
        self.csv = self.dir / "food.csv"
        with self.csv.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADER)
            writer.writerows(ROWS)
        self.research = self.dir / "research.json"
        self.research.write_text(json.dumps({"메뉴별 자료": [{"대표 음식": "갈비탕", "식약처 외식 자료집 제공량": [
            {"자료명": "자료집", "음식명": "갈비탕", "PDF 페이지": 433, "자료집 제공량_g": 600.0,
             "자료집 제공량당 영양": {"나트륨_mg": 1717.54}, "출처 URL": "https://example.org/a"}]}]}),
            encoding="utf-8")
        self.approvals = self.write_approvals(self.records())

    def records(self):
        return [
            {"standard_menu_id": "M1", "decision": "approved", "kind": "general", "food_code": "D301-1",
             "reason": "같은 음식"},
            {"standard_menu_id": "M2", "decision": "approved", "kind": "product", "food_code": "D212-1",
             "brand": "롯데리아", "product_name": "New치킨너겟", "reason": "같은 브랜드"},
            {"standard_menu_id": "M3", "decision": "hold", "reason": "가공식품"},
            {"standard_menu_id": "M4", "decision": "approved", "kind": "product_options", "brand": "브랜드",
             "product_name": "허니버터치킨", "source": "본사", "source_url": "https://example.org/b", "basis": 100,
             "unit": "g", "reason": "옵션 미확정",
             "options": [{"option": "뼈", "energy_kcal": 280, "protein_g": 20, "fat_g": None, "carbohydrate_g": None,
                          "sodium_mg": 340, "sugar_g": 4, "saturated_fat_g": 3},
                         {"option": "순살", "energy_kcal": 300, "protein_g": 21, "fat_g": None, "carbohydrate_g": None,
                          "sodium_mg": 319, "sugar_g": 3, "saturated_fat_g": 3}]},
        ]

    def write_approvals(self, records, name="approvals.json"):
        path = self.dir / name
        path.write_text(json.dumps({"records": records}, ensure_ascii=False), encoding="utf-8")
        return path

    def run_apply(self, approvals=None, source=None, name="out.sqlite3"):
        output = self.dir / name
        report = nutrition_links.apply(source or self.catalog, approvals or self.approvals, self.csv,
                                       self.research, output)
        with closing(sqlite3.connect(output)) as db:
            menus = {mid: json.loads(data) for mid, data in db.execute("SELECT id,data FROM catalog")}
        return report, menus

    def test_general_product_and_options_are_display_only(self):
        report, menus = self.run_apply()
        general = menus["M1"]["references"][0]
        self.assertEqual((general["kind"], general["basis"], general["unit"]), ("general", 100, "g"))
        self.assertEqual(general["values"], [166, 3.36, 37.33, 0.32, 0])
        self.assertIsNone(menus["M1"]["nutrition"])
        self.assertIsNone(general["serving_reference"])
        product = menus["M2"]["references"][0]
        self.assertEqual((product["kind"], product["brand"], product["product_name"]), ("product", "롯데리아",
                                                                                         "New치킨너겟"))
        self.assertEqual(product["values"], [237, 16.24, None, None, 430])  # 결측은 null
        options = menus["M4"]["references"]
        self.assertEqual([r["option"] for r in options], ["뼈", "순살"])
        self.assertEqual(menus["M4"]["nutrition_status"], nutrition_links.STATUS["product_options"])
        self.assertEqual(menus["M3"]["references"], [])  # 보류는 붙이지 않는다
        self.assertEqual(report["held"], ["M3"])
        self.assertEqual(menus["M5"]["nutrition"]["values"], [1, 2, 3, 4, 5])  # 점수용 값 유지

    def test_rerun_is_idempotent(self):
        first, _ = self.run_apply(name="a.sqlite3")
        second, menus = self.run_apply(source=self.dir / "a.sqlite3", name="b.sqlite3")
        self.assertEqual(first["catalog_logical_sha256"], second["catalog_logical_sha256"])
        self.assertEqual(second["counts"]["already_linked"], 3)
        self.assertEqual(len(menus["M4"]["references"]), 2)

    def test_survey_serving_is_separate_and_not_a_portion(self):
        _, menus = self.run_apply()
        serving = menus["M5"]["survey_servings"][0]
        self.assertEqual((serving["food"], serving["grams"]), ("갈비탕", 600.0))
        self.assertEqual(menus["M5"]["references"], [])
        self.assertNotIn("survey_servings", menus["M1"])

    def test_ml_basis_is_kept(self):
        records = [{"standard_menu_id": "M6", "decision": "approved", "kind": "general", "food_code": "D406-1",
                    "reason": "시험"}]
        with self.assertRaises(ValueError):  # 재료량 산출값은 일반 음식(분석)으로 붙이지 않는다
            self.run_apply(self.write_approvals(records, "ml.json"))
        self.assertEqual(nutrition_links.basis_of("100ml"), (100, "mL"))
        with self.assertRaises(ValueError):
            nutrition_links.basis_of("1인분(400g)")

    def test_rejects_processed_school_and_other_brand(self):
        cases = [
            {"standard_menu_id": "M3", "decision": "approved", "kind": "general", "food_code": "P117-1",
             "reason": "가공식품"},
            {"standard_menu_id": "M6", "decision": "approved", "kind": "general", "food_code": "D508-1",
             "reason": "급식"},
            {"standard_menu_id": "M2", "decision": "approved", "kind": "product", "food_code": "D212-1",
             "brand": "맥도날드", "product_name": "맥너겟", "reason": "다른 브랜드"},
        ]
        for i, record in enumerate(cases):
            with self.subTest(record["food_code"]), self.assertRaises(ValueError):
                self.run_apply(self.write_approvals([record], f"bad{i}.json"))

    def test_output_must_differ_from_source(self):
        with self.assertRaises(ValueError):
            nutrition_links.apply(self.catalog, self.approvals, self.csv, None, self.catalog)

    def test_committed_approvals_follow_policy(self):
        doc = json.loads(Path(nutrition_links.__file__).with_name("nutrition_approvals.json").read_text(
            encoding="utf-8"))
        records = doc["records"]
        self.assertEqual(len({r["standard_menu_id"] for r in records}), len(records))
        for record in records:
            self.assertIn(record["decision"], {"approved", "hold"})
            self.assertTrue(record["reason"])
            if record["decision"] == "approved" and record["kind"] != "product_options":
                self.assertRegex(record["food_code"], r"^D\d{3}-")  # 승인은 K-FIND 음식 코드만
        text = json.dumps(doc, ensure_ascii=False)
        self.assertNotIn("픽메뉴_통합자료", text)  # 내부 저장 경로를 넣지 않는다


if __name__ == "__main__":
    unittest.main()
