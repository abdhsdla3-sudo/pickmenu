import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import menu_stores
from recommend import Profile, families, recommend
from server import Handler

IDS = {"최상급 육회": "MN_" + "1" * 16, "[별미한점] 육회": "MN_" + "2" * 16,
       "불향가득제육": "MN_" + "3" * 16, "제육볶음 200g": "MN_" + "4" * 16}
COMPONENT = {"최상급 육회": "육회", "[별미한점] 육회": "육회", "불향가득제육": "제육볶음", "제육볶음 200g": "제육볶음"}
# (메뉴 이름, 매장 ID, 구, 리뷰 수)
TRACE = [("최상급 육회", "SW_a", "권선구", 5), ("최상급 육회", "SW_b", "영통구", 2),
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
        db.execute("CREATE TABLE review(review_id TEXT PRIMARY KEY, store_id TEXT, district TEXT)")
        db.execute("CREATE TABLE trace(menu_id TEXT, review_id TEXT, store_id TEXT, PRIMARY KEY(menu_id, review_id))")
        for name in IDS:
            db.execute("INSERT INTO catalog VALUES(?,?)", (IDS[name], json.dumps(menu(name), ensure_ascii=False)))
        serial = 0
        for name, store, district, count in TRACE:
            for _ in range(count):
                serial += 1
                db.execute("INSERT INTO review VALUES(?,?,?)", (f"R{serial}", store, district))
                db.execute("INSERT INTO trace VALUES(?,?,?)", (IDS[name], f"R{serial}", store))
        # 한 리뷰가 같은 대표 메뉴의 두 메뉴를 함께 언급하면 대표 메뉴 리뷰 수는 한 번만 센다.
        db.execute("INSERT INTO trace VALUES(?,?,?)", (IDS["[별미한점] 육회"], "R1", "SW_a"))
        db.commit()


class FamilyTest(unittest.TestCase):
    def test_menus_group_by_standard_food(self):
        menus = [menu(name) for name in IDS]
        result = recommend(menus, Profile.parse({}, ["고기"]))
        self.assertEqual([f["name"] for f in result["families"]], ["제육볶음", "육회"])
        jeyuk = result["families"][0]
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
        names = patch.object(menu_stores, "store_names", lambda: {"SW_a": "가 육회", "SW_b": "나 식당", "SW_c": "다 제육"})
        names.start()
        self.addCleanup(names.stop)
        facts = patch.object(menu_stores, "store_facts",
                             lambda: {"SW_a": {"repeat_ratio": 0.5, "positive_ratio": 0.9, "quantity": "많음"}})
        facts.start()
        self.addCleanup(facts.stop)

    def test_step_one_has_exact_family_evidence(self):
        status, result = self.call("/api/recommend", {})
        self.assertEqual(status, 200)
        by_name = {f["name"]: f for f in result["families"]}
        self.assertEqual(by_name["육회"]["evidence"], {"reviews": 8, "stores": 2})
        self.assertEqual(by_name["제육볶음"]["evidence"], {"reviews": 13, "stores": 2})
        self.assertNotIn("mapping_note", by_name["육회"]["best"])
        status, result = self.call("/api/recommend", {"district": "권선구"})
        self.assertEqual([(f["name"], f["evidence"]) for f in result["families"]],
                         [("육회", {"reviews": 6, "stores": 1})])

    def test_step_two_lists_menu_and_store(self):
        ids = [IDS["최상급 육회"], IDS["[별미한점] 육회"]]
        status, found = self.call("/api/recommend/stores", {"menu_ids": ids, "district": ""})
        self.assertEqual(status, 200)
        self.assertEqual([(s["menu_name"], s["store_name"], s["district"], s["reviews"]) for s in found["stores"]],
                         [("최상급 육회", "가 육회", "권선구", 5), ("최상급 육회", "나 식당", "영통구", 2),
                          ("[별미한점] 육회", "가 육회", "권선구", 2)])
        self.assertEqual((found["store_count"], found["citywide"], found["names_available"]), (2, False, True))
        # 추천이유 근거: 같은 메뉴를 파는 가게 수와 이 가게 몫(5/7), 참고자료 가게 근거
        self.assertEqual(found["stores"][0]["evidence"], {"menu_stores": 2, "menu_share": 0.714, "repeat_ratio": 0.5,
                                                          "positive_ratio": 0.9, "quantity": "많음"})
        self.assertEqual(found["stores"][1]["evidence"], {"menu_stores": 2, "menu_share": 0.286})
        self.assertNotIn("건", found["stores"][0]["comment_facts"])
        status, found = self.call("/api/recommend/stores", {"menu_ids": ids, "district": "영통구"})
        self.assertEqual([s["store_name"] for s in found["stores"]], ["나 식당"])
        # 고른 구에 없으면 수원 전체로 넓히고 알린다.
        status, found = self.call("/api/recommend/stores", {"menu_ids": ids, "district": "장안구"})
        self.assertEqual((found["citywide"], len(found["stores"])), (True, 3))
        # 매장 이름이 없는 가게는 null로 둔다.
        status, found = self.call("/api/recommend/stores", {"menu_ids": [IDS["제육볶음 200g"]], "district": "장안구"})
        self.assertEqual(found["stores"][0]["store_name"], None)

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

    def test_guest_mode_opens_only_choice_recommendation(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "PICKMENU_REQUIRE_LOGIN": "1", "PICKMENU_GUEST": "1", "PICKMENU_LLM_URL": "http://127.0.0.1:9",
                "PICKMENU_USERS_PATH": os.path.join(temp, "users.sqlite3")}):
            self.assertEqual(self.call("/api/recommend", {})[0], 200)
            with patch("llm.generate") as generate:
                status, found = self.call("/api/recommend/stores", {"menu_ids": [IDS["최상급 육회"]]})
            self.assertEqual(status, 200)
            generate.assert_not_called()  # 둘러보기는 AI 한마디 없이 사실 문장만
            self.assertEqual(found["stores"][0]["comment_mood"], "")
            # 대화(AI)·위치 검색·프로필 저장은 여전히 로그인이 필요하다.
            for path, body in (("/api/chat", {"messages": ["국밥"]}), ("/api/location/search", {"query": "수원역"}),
                               ("/api/v2/recommend", {})):
                with self.subTest(path=path):
                    self.assertEqual(self.call(path, body)[0], 401)
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
            connection.request("GET", "/api/auth/me")
            me = json.loads(connection.getresponse().read())
            connection.close()
            self.assertEqual((me["user"], me["guest"]), (None, True))


class StoreFactsTest(unittest.TestCase):
    def test_store_facts_aggregate_and_thresholds(self):
        rows = [("SW_a", 30, 27, 0.5, 40, "많음"), ("SW_a", 10, 8, 0.48, 10, "적음"),
                ("SW_b", 5, 5, 0.9, 5, "언급없음"), (None, 3, 3, None, 0, "보통")]

        class FakeDB:
            read_only = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query):
                return self

            def fetchall(self):
                return rows

        fake = type("psycopg", (), {"connect": staticmethod(lambda *a, **k: FakeDB())})
        with patch.dict("sys.modules", {"psycopg": fake}):
            facts = menu_stores.load_store_facts()
        # 리뷰 10건 이상일 때만 만족 비율, 재주문 표본 20건 이상일 때만 단골 비율, 양은 리뷰 가중 최다값
        self.assertEqual(facts, {"SW_a": {"positive_ratio": 0.875, "repeat_ratio": 0.5, "quantity": "많음"},
                                 "SW_b": {}})


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
