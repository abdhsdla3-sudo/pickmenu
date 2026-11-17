import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import menu_reco
import menu_stores
from recommend import Profile, families, recommend
from server import Handler

IDS = {"최상급 육회": "MN_" + "1" * 16, "[별미한점] 육회": "MN_" + "2" * 16,
       "불향가득제육": "MN_" + "3" * 16, "제육볶음 200g": "MN_" + "4" * 16}
COMPONENT = {"최상급 육회": "육회", "[별미한점] 육회": "육회", "불향가득제육": "제육볶음", "제육볶음 200g": "제육볶음"}
# (메뉴 이름, 매장 ID, 구, 리뷰 수). SW_e는 SW_a와 같은 가게의 다른 배달앱 ID다.
PLATFORM = {"SW_e": "CPEATS"}
TRACE = [("최상급 육회", "SW_a", "권선구", 5), ("최상급 육회", "SW_b", "영통구", 2), ("최상급 육회", "SW_e", "권선구", 4),
         ("[별미한점] 육회", "SW_a", "권선구", 1), ("불향가득제육", "SW_c", "영통구", 9),
         ("제육볶음 200g", "SW_c", "영통구", 1), ("제육볶음 200g", "SW_d", "장안구", 3)]


def menu(name):
    regions = {"수원시": {"reviews": 0, "stores": 0}}
    for menu_name, store, district, count in TRACE:
        if menu_name == name:
            regions["수원시"]["reviews"] += count
            regions.setdefault(district, {"reviews": 0, "stores": 0})["reviews"] += count
    return {"name": name, "standard_menu_id": IDS[name], "category": "고기", "components": [COMPONENT[name]],
            "compound": False, "spice": "", "soup": "no", "nutrition": None, "references": [],
            "nutrition_status": "자료 없음", "mapping_method": "", "mapping_note": "", "regions": regions}


def build_catalog(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE catalog(id TEXT PRIMARY KEY, data TEXT)")
        db.execute("CREATE TABLE review(review_id TEXT PRIMARY KEY, store_id TEXT, district TEXT, platform TEXT)")
        db.execute("CREATE TABLE trace(menu_id TEXT, review_id TEXT, store_id TEXT, PRIMARY KEY(menu_id, review_id))")
        for name in IDS:
            db.execute("INSERT INTO catalog VALUES(?,?)", (IDS[name], json.dumps(menu(name), ensure_ascii=False)))
        serial = 0
        for name, store, district, count in TRACE:
            for _ in range(count):
                serial += 1
                db.execute("INSERT INTO review VALUES(?,?,?,?)", (f"R{serial}", store, district, PLATFORM.get(store, "BAEMIN")))
                db.execute("INSERT INTO trace VALUES(?,?,?)", (IDS[name], f"R{serial}", store))
        # 한 리뷰가 같은 대표 메뉴의 두 메뉴를 함께 언급하면 대표 메뉴 리뷰 수는 한 번만 센다.
        db.execute("INSERT INTO trace VALUES(?,?,?)", (IDS["[별미한점] 육회"], "R1", "SW_a"))
        db.commit()


class FamilyTest(unittest.TestCase):
    def test_menus_group_by_standard_food(self):
        menus = [menu(name) for name in IDS]
        result = recommend(menus, Profile.parse({}, ["고기"]))
        self.assertEqual([f["name"] for f in result["families"]], ["육회", "제육볶음"])
        jeyuk = result["families"][1]
        self.assertEqual((jeyuk["menu_count"], jeyuk["best"]["name"]), (2, "불향가득제육"))
        self.assertEqual([m["name"] for m in jeyuk["members"]], ["불향가득제육", "제육볶음 200g"])
        self.assertTrue(all(m["reasons"] for m in jeyuk["members"]))
        # 검색어로 거른 메뉴만 대표 메뉴에 남는다.
        found = recommend(menus, Profile.parse({"query": "최상급"}, ["고기"]))["families"]
        self.assertEqual([(f["name"], [m["name"] for m in f["members"]]) for f in found], [("육회", ["최상급 육회"])])

    def test_limits(self):
        ranked = [{**menu("최상급 육회"), "components": [f"음식{i}"], "reasons": []} for i in range(8)]
        self.assertEqual(len(families(ranked)), 5)
        same = [{**menu("최상급 육회"), "standard_menu_id": f"MN_{i:016x}", "reasons": []} for i in range(40)]
        self.assertEqual((families(same)[0]["menu_count"], len(families(same)[0]["members"])), (40, 30))


class StoreApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "catalog.sqlite3"
        build_catalog(cls.path)

        class Quiet(Handler):
            def log_message(self, format, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def call(self, path, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request("POST", path, body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def setUp(self):
        env = patch.dict(os.environ, {"PICKMENU_CATALOG_PATH": str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        for key in ("PICKMENU_API_TOKEN", "PICKMENU_REQUIRE_LOGIN"):
            os.environ.pop(key, None)
        names = patch.object(menu_stores, "store_names", lambda: {"SW_a": "가 육회", "SW_b": "나 식당", "SW_c": "다 제육", "SW_e": "가-육회"})
        names.start()
        self.addCleanup(names.stop)
        reference = patch.object(menu_reco, "data", side_effect=OSError("no db"))
        reference.start()
        self.addCleanup(reference.stop)

    def test_step_one_has_exact_family_evidence(self):
        status, result = self.call("/api/recommend", {})
        self.assertEqual(status, 200)
        by_name = {f["name"]: f for f in result["families"]}
        self.assertEqual(by_name["육회"]["evidence"], {"reviews": 12, "stores": 2})
        self.assertEqual(by_name["제육볶음"]["evidence"], {"reviews": 13, "stores": 2})
        self.assertNotIn("mapping_note", by_name["육회"]["best"])
        status, result = self.call("/api/recommend", {"district": "권선구"})
        self.assertEqual([(f["name"], f["evidence"]) for f in result["families"]],
                         [("육회", {"reviews": 10, "stores": 1})])

    def test_step_two_lists_menu_and_store(self):
        ids = [IDS["최상급 육회"], IDS["[별미한점] 육회"]]
        status, found = self.call("/api/recommend/stores", {"menu_ids": ids, "district": ""})
        self.assertEqual(status, 200)
        # 같은 구·같은 이름(기호 무시)의 배달앱별 ID는 한 가게로 묶고 리뷰 수를 더한다.
        self.assertEqual([(s["menu_name"], s["store_name"], s["district"], s["reviews"], s["platforms"])
                          for s in found["stores"]],
                         [("최상급 육회", "가 육회", "권선구", 9, ["배민", "쿠팡이츠"]),
                          ("최상급 육회", "나 식당", "영통구", 2, ["배민"]),
                          ("[별미한점] 육회", "가 육회", "권선구", 2, ["배민"])])
        self.assertEqual(found["stores"][0]["store_ids"], 2)
        self.assertEqual((found["store_count"], found["citywide"], found["names_available"]), (2, False, True))
        status, found = self.call("/api/recommend/stores", {"menu_ids": ids, "district": "영통구"})
        self.assertEqual([s["store_name"] for s in found["stores"]], ["나 식당"])
        # 고른 구에 없으면 수원 전체로 넓히고 알린다.
        status, found = self.call("/api/recommend/stores", {"menu_ids": ids, "district": "장안구"})
        self.assertEqual((found["citywide"], len(found["stores"])), (True, 3))
        # 매장 이름이 없는 가게는 null로 둔다.
        status, found = self.call("/api/recommend/stores", {"menu_ids": [IDS["제육볶음 200g"]], "district": "장안구"})
        self.assertEqual(found["stores"][0]["store_name"], None)

    def test_step_two_adds_reference_ratings(self):
        def row(store, count, rating):
            return {"integrated_store_id": store, "detail_food": "육회", "upper_food": "육회", "review_count": count,
                    "rating_display": Decimal(rating), "disappointing_reviews": 1, "few_reviews": False,
                    "store_repeat_ratio": Decimal("0.5")}

        data = SimpleNamespace(menus=[row("SW_a", 10, "4.2"), row("SW_e", 30, "4.7"),
                                      {**row("SW_b", 50, "3.9"), "detail_food": "육회비빔밥", "upper_food": "비빔밥"}])
        with patch.object(menu_reco, "data", lambda: data):
            status, found = self.call("/api/recommend/stores", {"menu_ids": [IDS["최상급 육회"]], "district": ""})
        self.assertEqual(status, 200)
        # 묶인 가게(SW_a·SW_e)는 기준표 리뷰가 더 많은 값을, 다른 음식 행은 쓰지 않는다.
        self.assertEqual(found["stores"][0]["rating"], {"rating_display": 4.7, "review_count": 30,
                                                        "disappointing_reviews": 1, "few_reviews": False,
                                                        "store_repeat_ratio": 0.5})
        self.assertIsNone(found["stores"][1]["rating"])
        # 기준표 DB가 없으면 별점 없이 보여 준다.
        status, found = self.call("/api/recommend/stores", {"menu_ids": [IDS["최상급 육회"]], "district": ""})
        self.assertEqual((status, found["stores"][0]["rating"]), (200, None))

    def test_step_two_without_store_names(self):
        with patch.object(menu_stores, "store_names", lambda: None):
            status, found = self.call("/api/recommend/stores", {"menu_ids": [IDS["불향가득제육"]], "district": ""})
        self.assertEqual((status, found["names_available"], found["stores"][0]["store_name"]), (200, False, None))

    def test_step_two_rejects_bad_input(self):
        for body in ({}, {"menu_ids": []}, {"menu_ids": ["x"]}, {"menu_ids": [IDS["최상급 육회"]], "district": "서울"},
                     {"menu_ids": [IDS["최상급 육회"]] * 31}, ["MN_"]):
            with self.subTest(body=body):
                self.assertEqual(self.call("/api/recommend/stores", body)[0], 400)
        self.assertEqual(self.call("/api/recommend/stores", {"menu_ids": ["MN_" + "f" * 16]})[0], 404)

    def test_login_required(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "PICKMENU_REQUIRE_LOGIN": "1", "PICKMENU_USERS_PATH": os.path.join(temp, "users.sqlite3")}):
            self.assertEqual(self.call("/api/recommend/stores", {"menu_ids": [IDS["최상급 육회"]]})[0], 401)


class NameKeyTest(unittest.TestCase):
    def test_store_names_compare_without_symbols(self):
        self.assertEqual(menu_stores.name_key("제육대가 수원점"), menu_stores.name_key("제육대가-수원점"))
        self.assertNotEqual(menu_stores.name_key("제육대가 수원점"), menu_stores.name_key("불족대가 수원점"))
        names = {"SW_a": "가 점", "SW_b": "가-점"}
        self.assertEqual(menu_stores.store_key("SW_a", "권선구", names), menu_stores.store_key("SW_b", "권선구", names))
        self.assertNotEqual(menu_stores.store_key("SW_a", "권선구", names), menu_stores.store_key("SW_b", "영통구", names))
        self.assertEqual(menu_stores.store_key("SW_x", "권선구", None), ("id", "SW_x"))

    def test_branchless_name_joins_single_branch_only(self):
        names = {"A": "불족대가", "B": "불족대가 수원점", "C": "롯데리아", "D": "롯데리아 매탄점",
                 "E": "롯데리아-영통점"}
        keys = menu_stores.store_keys({(sid, "권선구") for sid in names}, names)
        self.assertEqual(keys[("A", "권선구")], keys[("B", "권선구")])
        # 같은 구에 지점이 둘 이상이면 지점명 없는 이름을 어느 쪽에도 합치지 않는다.
        self.assertEqual(len({keys[(sid, "권선구")] for sid in "CDE"}), 3)
        other = menu_stores.store_keys({("A", "영통구"), ("B", "권선구")}, names)
        self.assertNotEqual(other[("A", "영통구")], other[("B", "권선구")])


class StoreNameCacheTest(unittest.TestCase):
    def test_failure_returns_none_and_retries_later(self):
        with patch.dict(menu_stores._names, {"value": None, "checked": float("-inf")}):
            with patch.object(menu_stores, "load_store_names", side_effect=OSError("no db")):
                self.assertIsNone(menu_stores.store_names())
            with patch.object(menu_stores, "load_store_names", return_value={"SW_a": "가"}):
                self.assertIsNone(menu_stores.store_names())  # 재시도 대기 중
                menu_stores._names["checked"] = float("-inf")
                self.assertEqual(menu_stores.store_names(), {"SW_a": "가"})


if __name__ == "__main__":
    unittest.main()
