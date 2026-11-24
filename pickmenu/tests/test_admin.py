import http.client
import io
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

import admin
import auth
import llm
import server
from server import SESSION_COOKIE, VISITOR_COOKIE, Handler

MENUS = [
    {"standard_menu_id": "MN_00000000000000a1", "name": "김치찌개 정식", "category": "찌개·탕",
     "components": ["김치찌개"], "spice": "hot", "soup": "yes"},
    {"standard_menu_id": "MN_00000000000000b2", "name": "순대국밥", "category": "국밥·해장국",
     "components": ["순대국밥"], "spice": "", "soup": "yes"},
]


class Quiet(Handler):
    def log_message(self, format, *args):
        pass


class AdminStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PICKMENU_ADMIN_DB_PATH": str(Path(self.temp.name) / "admin.sqlite3")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_summary_counts_visitors_returning_and_searches(self):
        with patch("admin.now", return_value=1_800_000_000 - 3 * 86400):
            admin.record_event("visit", "visitor-aaaaaaaaaaaaaaaa")
        with patch("admin.now", return_value=1_800_000_000):
            admin.record_event("visit", "visitor-aaaaaaaaaaaaaaaa")
            admin.record_event("visit", "visitor-bbbbbbbbbbbbbbbb")
            admin.record_event("recommend", "visitor-bbbbbbbbbbbbbbbb", "국밥|국밥·해장국")
            admin.record_event("chat", "visitor-aaaaaaaaaaaaaaaa", "국밥")
            admin.record_event("menu_click", "visitor-aaaaaaaaaaaaaaaa", "국밥")
            admin.record_event("unknown", "visitor-aaaaaaaaaaaaaaaa", "무시")
            admin.record_ai_call("조건 추출", "m", 300, 100, 20, "ok")
            admin.record_ai_call("조건 추출", "m", 0, None, None, "busy")
            data = admin.summary(7)
        self.assertEqual(data["totals"]["visitors"], 2)
        self.assertEqual(data["totals"]["returning"], 1)
        self.assertEqual((data["totals"]["recommend"], data["totals"]["chat"]), (1, 1))
        self.assertEqual(data["top_searches"][0], ("국밥", 2))
        self.assertEqual(data["top_menus"], [("국밥", 1)])
        self.assertEqual((data["ai"]["calls"], data["ai"]["errors"], data["ai"]["avg_ms"]), (2, 1, 300))
        self.assertEqual(data["ai"]["prompt_tokens"] + data["ai"]["output_tokens"], 120)
        self.assertEqual(len(data["daily"]), 7)

    def test_feedback_review_and_menu_override_leave_audit(self):
        with self.assertRaises(ValueError):
            admin.add_feedback({"kind": "error", "message": ""})
        with self.assertRaises(ValueError):
            admin.add_feedback({"kind": "nope", "message": "내용"})
        fid = admin.add_feedback({"kind": "bad_recommendation", "message": "",
                                  "context": {"mode": "chat", "menus": ["국밥"], "conditions": "국물"}})
        admin.update_feedback(fid, {"status": "done", "note": "확인"}, "a***")
        self.assertEqual(admin.feedback_rows("done")[0]["context"]["menus"], ["국밥"])
        with self.assertRaises(LookupError):
            admin.update_feedback(999, {"status": "done"}, "a***")

        admin.record_chat(["점심에 순대국밥 먹었어"], {"query": "순대국밥"}, {"avoid_menus": "순대국밥"}, ["죽"], False)
        chat = admin.chats()[0]
        self.assertEqual((chat["ai_output"], chat["status"]), ({"query": "순대국밥"}, "unchecked"))
        admin.review_chat(chat["id"], {"status": "wrong", "note": "죽보다 가벼운 것"}, "a***")
        self.assertEqual(admin.chats("wrong")[0]["note"], "죽보다 가벼운 것")

        admin.update_menu("MN_00000000000000a1", {"hidden": True, "display_name": "", "note": "판매 종료"},
                          "a***", "김치찌개 정식")
        admin.update_menu("MN_00000000000000b2", {"hidden": False, "display_name": "순대국", "note": ""},
                          "a***", "순대국밥")
        shown = admin.apply_overrides(MENUS)
        self.assertEqual([menu["name"] for menu in shown], ["순대국"])
        self.assertEqual(MENUS[1]["name"], "순대국밥")  # 원본은 그대로
        self.assertEqual(len(admin.menu_rows(MENUS, "")), 2)
        actions = [row["action"] for row in admin.audit_rows()]
        self.assertEqual(actions.count("메뉴 수정"), 2)
        self.assertIn("AI 결과 검수", actions)
        self.assertIn("피드백 처리", actions)

    def test_old_chats_are_deleted(self):
        with patch("admin.now", return_value=1_800_000_000 - (admin.CHAT_DAYS + 1) * 86400):
            admin.record_chat(["오래된 대화"], None, {}, [], False)
        with patch("admin.now", return_value=1_800_000_000):
            admin.record_chat(["새 대화"], None, {}, [], True)
            self.assertEqual([chat["messages"] for chat in admin.chats()], [["새 대화"]])


class AiCallLogTest(unittest.TestCase):
    def test_generate_reports_tokens_and_failures(self):
        calls = []
        body = json.dumps({"message": {"content": json.dumps({"query": "국밥"})},
                           "prompt_eval_count": 410, "eval_count": 12}).encode()

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with (patch.dict(os.environ, {"PICKMENU_LLM_URL": "http://llm", "PICKMENU_LLM_MODEL": "m"}),
              patch.object(llm, "on_call", lambda *args: calls.append(args)),
              patch("llm.urlopen", return_value=Response(body))):
            llm.reset_last_output()
            self.assertEqual(llm.generate(llm.CONDITION_PROMPT, {}), {"query": "국밥"})
            self.assertEqual(llm.last_output(), {"query": "국밥"})
        self.assertEqual((calls[0][0], calls[0][3], calls[0][4], calls[0][5]), ("조건 추출", 410, 12, "ok"))
        with (patch.dict(os.environ, {"PICKMENU_LLM_URL": "http://llm"}),
              patch.object(llm, "on_call", lambda *args: calls.append(args)),
              patch("llm.urlopen", side_effect=TimeoutError("timed out"))):
            with self.assertRaises(llm.Unavailable):
                llm.generate("가게 소개", {})
        self.assertEqual((calls[1][0], calls[1][5]), ("매장 소개", "timeout"))


class AdminHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "PICKMENU_USERS_PATH": str(folder / "users.sqlite3"),
            "PICKMENU_ACCOUNT_KEY_PATH": str(folder / "accounts.key"),
            "PICKMENU_ADMIN_DB_PATH": str(folder / "admin.sqlite3"),
            "PICKMENU_REQUIRE_LOGIN": "1", "PICKMENU_GUEST": "1",
        })
        self.env.start()
        os.environ.pop("PICKMENU_API_TOKEN", None)
        self.menus = patch("server.prepared_menus", return_value=MENUS)
        self.menus.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.menus.stop()
        self.env.stop()
        self.temp.cleanup()

    def call(self, method, path, body=None, cookie=""):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        data = response.read()
        cookies = response.headers.get_all("Set-Cookie") or []
        connection.close()
        try:
            value = json.loads(data)
        except ValueError:
            value = data.decode("utf-8")
        return response.status, value, cookies

    def test_admin_requires_granted_account(self):
        self.assertEqual(self.call("GET", "/api/admin/me")[0], 401)
        token, _ = auth.signup({"username": "judge_01", "password": "password1", "profile": {"nickname": "심사"}})
        cookie = f"{SESSION_COOKIE}={token}"
        self.assertEqual(self.call("GET", "/api/admin/summary", cookie=cookie)[0], 403)
        with closing(auth.connect()) as db:
            user_id = db.execute("SELECT id FROM users").fetchone()[0]
        admin.grant(user_id, "j***")
        status, me, _ = self.call("GET", "/api/admin/me", cookie=cookie)
        self.assertEqual((status, me["user"]), (200, "j***"))
        self.assertEqual(self.call("GET", "/api/admin/summary?days=7", cookie=cookie)[1]["days"], 7)

        # 메뉴 숨김: 일반 메뉴 상세에서 사라지고, 관리 화면 검색에는 남는다.
        status, _, _ = self.call("POST", "/api/admin/menus/MN_00000000000000a1",
                                 {"hidden": True, "display_name": "", "note": "판매 종료"}, cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self.call("GET", "/api/menus/MN_00000000000000a1", cookie=cookie)[0], 404)
        self.assertTrue(self.call("GET", "/api/admin/menus?q=%EA%B9%80%EC%B9%98", cookie=cookie)[1]["menus"][0]["hidden"])
        self.assertEqual(self.call("POST", "/api/admin/menus/MN_0000000000000zz9", {"hidden": True}, cookie)[0], 404)
        admin.revoke(user_id, "j***")
        self.assertEqual(self.call("GET", "/api/admin/me", cookie=cookie)[0], 403)

    def test_visitor_cookie_events_and_feedback_without_login(self):
        status, _, cookies = self.call("GET", "/")
        self.assertEqual(status, 200)
        visitor = next(c for c in cookies if c.startswith(VISITOR_COOKIE)).split(";")[0]
        self.assertIn("HttpOnly", next(c for c in cookies if c.startswith(VISITOR_COOKIE)))
        self.assertEqual(self.call("GET", "/", cookie=visitor)[2], [])  # 이미 있으면 다시 주지 않는다
        self.assertEqual(self.call("POST", "/api/events", {"kind": "visit"}, visitor)[0], 200)
        self.assertEqual(self.call("POST", "/api/events", {"kind": "chat", "detail": "x"}, visitor)[0], 400)
        self.assertEqual(self.call("POST", "/api/feedback", {"kind": "error", "message": "지도가 안 떠요"})[0], 201)
        self.assertEqual(self.call("POST", "/api/feedback", {"kind": "error", "message": ""})[0], 400)
        with closing(sqlite3.connect(os.environ["PICKMENU_ADMIN_DB_PATH"])) as db:
            self.assertEqual(db.execute("SELECT kind, visitor FROM events").fetchall(),
                             [("visit", visitor.split("=", 1)[1])])
        status, page, _ = self.call("GET", "/admin")
        self.assertEqual(status, 200)
        self.assertIn("관리자 로그인", page)


class AssetsTest(unittest.TestCase):
    def test_admin_assets_exist(self):
        for path in ("/admin", "/admin.js", "/admin.css"):
            self.assertTrue((server.BASE / "web" / server.ASSETS[path][0]).exists())


if __name__ == "__main__":
    unittest.main()
