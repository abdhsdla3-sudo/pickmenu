import os
import unittest
from collections import defaultdict

import menu_reco
from menu_reco import Data, Query, recommend

# 추천예시.csv의 프로필 이름 → 요청 조건. P4(채식)는 제공하지 않는 조건이라 따로 확인한다.
PROFILES = {
    "P1": dict(goal="balance"),
    "P2": dict(goal="protein", groups=["고기", "닭요리"], district="영통구"),
    "P2+": dict(goal="protein", groups=["고기", "닭요리"]),
    "P3": dict(goal="balance", groups=["면", "분식"], district="권선구"),
    "P5": dict(goal="protein", district="팔달구"),
    "P6": dict(diet="meat", goal="balance", groups=["치킨", "피자"], district="장안구"),
    "S1": dict(goal="balance", spice="mild"),
    "S2": dict(goal="balance", groups=["면", "분식"], spice="mild", district="권선구"),
    "S3": dict(goal="protein", spice="mild", district="영통구"),
    "S4": dict(goal="balance", groups=["중식", "국밥·해장국"], broth="yes", spice="mild"),
    "N1": dict(goal="protein", district="권선구"),
    "N2": dict(goal="balance", groups=["국밥·해장국", "찌개·탕"], broth="yes", district="장안구"),
    "N3": dict(goal="balance", groups=["일식", "해산물"], spice="mild", district="영통구"),
    "N4": dict(diet="meat", goal="protein", groups=["중식", "분식"], district="팔달구"),
    "N5": dict(goal="balance", groups=["샐러드·건강식", "양식"], broth="no"),
    "N6": dict(goal="protein", groups=["버거·샌드위치", "피자"], spice="mild", district="영통구"),
    "N7": dict(goal="balance", groups=["전·부침"], district="팔달구"),
    "E1": dict(goal="protein", spice="mild", broth="yes", district="장안구"),
    "E4": dict(goal="balance", groups=["죽"], district="팔달구"),
    "E7": dict(goal="balance", groups=["찌개·탕", "국밥·해장국"], broth="yes", spice="mild", district="권선구"),
    "E10": dict(goal="balance", groups=["일식", "중식", "면"], spice="mild", broth="no", district="팔달구"),
}


def menu(store, column, district="권선구", reviews=20, score=0.1, spice="안 매움", broth="없음",
         balance=50.0, rating=4.7):
    return {"integrated_store_id": store, "store_name": store, "district": district, "column_name": column,
            "detail_food": column, "display_name": column, "rating_display": round(rating, 1),
            "rating": rating, "review_count": reviews, "few_reviews": reviews < 10,
            "disappointing_reviews": 1, "store_repeat_ratio": 0.4, "recommendation_score": score,
            "spiciness": spice, "broth": broth, "balance_score": balance, "protein_score": 20.0,
            "rice_included": False, "portion_g": None}


def fixture():
    menus = [menu(f"A{i}", "국밥", reviews=30, broth="있음", balance=30.0) for i in range(3)]
    menus += [menu(f"B{i}", "김밥", reviews=40, score=i / 10) for i in range(4)]
    menus += [menu("C0", "떡볶이", spice="매움", reviews=10), menu("C1", "떡볶이", reviews=10),
              menu("C2", "떡볶이", district="영통구", reviews=10)]
    menus += [menu("B0", "김밥", reviews=5, score=2.0)]
    columns = {name: {"display_name": name, "is_meal": True, "is_side": False, "eligible": True,
                      "meat_preferred": False, "groups": group}
               for name, group in (("국밥", "국밥·해장국"), ("김밥", "분식"), ("떡볶이", "분식"))}
    return Data(menus, columns, {"국밥·해장국": {"국밥"}, "분식": {"김밥", "떡볶이"}},
                ["국밥·해장국", "분식"])


class RulesTest(unittest.TestCase):
    def test_parse(self):
        groups = {"분식", "전·부침"}
        query = Query.parse({"district": "권선구", "goal": "protein", "groups": ["분식"], "spice": "mild"},
                            {"분식"}, groups)
        self.assertEqual((query.district, query.goal, query.groups, query.mild), ("권선구", "protein", ("분식",), True))
        for bad in ({"diet": "vegan"}, {"goal": "light"}, {"district": "서울"}, {"groups": ["없음"]}, []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Query.parse(bad, {"분식"}, groups)

    def test_grades_filters_and_store_order(self):
        data = fixture()
        result = recommend(data, Query(district="권선구"))
        names = [item["column"] for item in result["items"]]
        # 국밥은 목표 점수가 낮아 뒤로, 떡볶이는 가게 2곳·리뷰 30건 미만이라 빠진다. 골고루면 분식은 1개만.
        self.assertEqual(names, ["김밥", "국밥"])
        stores = result["items"][0]["stores"]
        # B0은 리뷰가 많은 메뉴(40건)를 대표로 쓰므로 리뷰 5건짜리 높은 점수 메뉴로 올라가지 않는다.
        self.assertEqual([s["store_id"] for s in stores], ["B3", "B2", "B1", "B0"])
        self.assertEqual(result["items"][1]["notes"], ["목표 점수 낮음"])
        mild = recommend(data, Query(district="권선구", groups=("분식",), mild=True))
        self.assertEqual([i["column"] for i in mild["items"]], ["김밥", "국밥"])
        self.assertEqual(mild["unavailable"], ["떡볶이"])
        city = recommend(data, Query(groups=("분식",), mild=True))
        self.assertEqual(city["unavailable"], [])
        broth = recommend(data, Query(broth="yes"))
        self.assertEqual([i["column"] for i in broth["items"]], ["국밥"])


class ApiTest(unittest.TestCase):
    def test_v2_endpoints(self):
        import http.client
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch

        from server import Handler

        class Quiet(Handler):
            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def call(method, path, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            data = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result

        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PICKMENU_API_TOKEN", None)
                with patch.object(menu_reco, "data", fixture):
                    status, options = call("GET", "/api/v2/options")
                    self.assertEqual((status, [g["name"] for g in options["groups"]]), (200, ["국밥·해장국", "분식"]))
                    status, result = call("POST", "/api/v2/recommend", {"district": "권선구"})
                    self.assertEqual((status, [i["column"] for i in result["items"]]), (200, ["김밥", "국밥"]))
                    self.assertIn("store_repeat_ratio", result["items"][0]["stores"][0])
                    self.assertNotIn("rating", result["items"][0]["stores"][0])
                    self.assertEqual(call("POST", "/api/v2/recommend", {"diet": "vegan"})[0], 400)

                def broken():
                    raise OSError("no db")

                with patch.object(menu_reco, "data", broken):
                    self.assertEqual(call("POST", "/api/v2/recommend", {})[0], 503)
        finally:
            server.shutdown()
            server.server_close()


@unittest.skipUnless(os.environ.get("PICKMENU_REFERENCE_TEST") == "1",
                     "PICKMENU_REFERENCE_TEST=1과 DB 연결이 있을 때만 추천예시와 대조한다")
class ExamplesTest(unittest.TestCase):
    """DB의 추천예시(데이터 담당 recommend() 결과)와 같은 결과를 내는지 확인한다."""

    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row

        cls.data = menu_reco.load_from_db()
        with psycopg.connect(menu_reco.dsn(), row_factory=dict_row) as db:
            rows = db.execute("SELECT data FROM pickmenu_data.current_recommendation_examples "
                              "ORDER BY row_number").fetchall()
        cls.examples = defaultdict(list)
        for row in rows:
            cls.examples[row["data"]["프로필"].split(" ")[0].split("(")[0]].append(row["data"])

    def test_profiles(self):
        for code, condition in PROFILES.items():
            expected = self.examples[code]
            with self.subTest(profile=code):
                query = Query.parse(condition, set(self.data.visible_groups), set(self.data.group_columns) | {"전·부침"})
                result = recommend(self.data, query, store_limit=3)
                got = [(i["column"], i["store_count"], i["review_count"], i["nutrition_score"],
                        round(i["rating_level"], 3), i["column_score"], " · ".join(i["notes"]))
                       for i in result["items"]]
                want = [(e["컬럼"], int(e["가게수"]), int(e["리뷰수"]), float(e["영양점수"]),
                         round(float(e["별점수준"]), 3), float(e["컬럼점수"]), e["비고"]) for e in expected]
                self.assertEqual(got, want)
                first = expected[0]
                if first["넓히기버튼"] in {"예", "아니오"}:
                    self.assertEqual(result["expand"], first["넓히기버튼"] == "예")
                    self.assertEqual(result["expand_reason"], first["넓히기사유"])
                hints = "; ".join(f"{h['column']}({h['message']})" for h in result["expand_hints"])
                self.assertEqual(hints, first["넓히기안내"])
                self.assertEqual(",".join(result["unavailable"]), first["조건으로없음"])
                stats = menu_reco.column_stats(self.data, query, query.district)
                for item, row in zip(result["items"], expected):
                    ranked = menu_reco.store_ranking(stats[item["column"]].menus, query)[:3]
                    shown = " | ".join(
                        f"{s['store_name']}({s['district']}) {s['display_name']} ★{s['rating']:.2f}"
                        f"({s['review_count']}{' · 리뷰 적음' if s['few_reviews'] else ''})"
                        for s in ranked)
                    self.assertEqual(shown, row["가게메뉴_상위3(가게당1줄)"])

    def test_vegetarian_is_refused(self):
        with self.assertRaises(ValueError):
            Query.parse({"diet": "vegetarian"}, set(), set())


if __name__ == "__main__":
    unittest.main()
