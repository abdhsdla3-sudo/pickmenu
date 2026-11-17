import http.client
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import auth
from migrate_accounts import migrate
from server import SESSION_COOKIE, Handler


def registration(username, password="password1"):
    return {"username": username, "password": password, "profile": {
        "nickname": "검증닉네임", "age": "30", "height": "170", "weight": "65",
        "gender": "undisclosed"}}


class Quiet(Handler):
    def log_message(self, format, *args):
        pass


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PICKMENU_USERS_PATH": str(Path(self.temp.name) / "users.sqlite3"),
            "PICKMENU_ACCOUNT_KEY_PATH": str(Path(self.temp.name) / "accounts.key"),
        })
        self.env.start()
        auth._failures.clear()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_password_hash(self):
        stored = auth.hash_password("비밀번호1234")
        self.assertNotIn("비밀번호1234", stored)
        self.assertTrue(auth.verify_password("비밀번호1234", stored))
        self.assertFalse(auth.verify_password("다른비밀번호", stored))
        self.assertFalse(auth.verify_password("x", "broken"))

    def test_signup_login_session(self):
        token, account = auth.signup(registration(" Menu_01 "))
        self.assertEqual(account["user"], {"username": "m***"})
        self.assertEqual(account["profile"]["nickname"], "검증닉네임")
        self.assertEqual(auth.current(token)[1]["user"]["username"], "m***")
        with self.assertRaises(auth.AuthError) as caught:
            auth.signup(registration("menu_01", "password2"))
        self.assertEqual(caught.exception.status, 409)
        for bad in ({"username": "ab", "password": "password1"}, {"username": "한글아이디", "password": "password1"},
                    {"username": "menu_02", "password": "short"}, {"username": 1, "password": "password1"}, []):
            with self.subTest(bad=bad), self.assertRaises(auth.AuthError):
                auth.signup(bad)
        with self.assertRaises(auth.AuthError) as caught:
            auth.login({"username": "menu_01", "password": "wrong-pass"})
        self.assertEqual(caught.exception.status, 401)
        token2, _ = auth.login({"username": "MENU_01", "password": "password1"})
        auth.logout(token2)
        self.assertIsNone(auth.current(token2))
        self.assertIsNotNone(auth.current(token))
        self.assertIsNone(auth.current("없는토큰"))

    def test_login_lock(self):
        auth.signup(registration("locked"))
        for _ in range(auth.LOCK_FAILURES):
            with self.assertRaises(auth.AuthError):
                auth.login({"username": "locked", "password": "wrong-pass"})
        with self.assertRaises(auth.AuthError) as caught:
            auth.login({"username": "locked", "password": "password1"})
        self.assertEqual(caught.exception.status, 429)

    def test_profile_keeps_only_allowed_fields(self):
        token, _ = auth.signup(registration("profile"))
        user_id = auth.current(token)[0]
        saved = auth.save_profile(user_id, {"nickname": "메뉴고민러", "diet": "vegan", "age": "30",
                                            "allergy": "땅콩"})
        self.assertEqual(saved["age"], "30")
        self.assertEqual(saved["gender"], "undisclosed")
        self.assertEqual(auth.current(token)[1]["profile"]["allergy"], "땅콩")
        with self.assertRaises(auth.AuthError):
            auth.save_profile(user_id, {"allergy": "가" * 101})
        self.assertIsNone(auth.save_profile(user_id, None))
        self.assertIsNone(auth.current(token)[1]["profile"])

    def test_signup_requires_basics_and_validates_ranges(self):
        for field in ["nickname", "age", "height", "weight", "gender"]:
            value = registration("basics")
            del value["profile"][field]
            with self.subTest(field=field), self.assertRaises(auth.AuthError):
                auth.signup(value)
        for field, item in [("age", "1.5"), ("age", "121"), ("height", "NaN"),
                            ("weight", "Infinity"), ("weight", "0"), ("gender", "unknown")]:
            value = registration("basics")
            value["profile"][field] = item
            with self.subTest(field=field, item=item), self.assertRaises(auth.AuthError):
                auth.signup(value)
        _, account = auth.signup(registration("basics"))
        self.assertEqual(account["profile"]["goal"], "balance")

    def test_plaintext_is_not_stored_and_ciphertext_is_bound_to_user(self):
        token, _ = auth.signup(registration("private_user"))
        other, _ = auth.signup(registration("other_user"))
        first_id = auth.current(token)[0]
        second_id = auth.current(other)[0]
        with auth.connect() as db:
            row = db.execute("SELECT username,profile,username_masked FROM users WHERE id=?", (first_id,)).fetchone()
            self.assertTrue(row[0].startswith("u1:"))
            self.assertTrue(row[1].startswith("enc:v1:"))
            self.assertEqual(row[2], "p***")
            db.execute("UPDATE users SET profile=? WHERE id=?", (row[1], second_id))
        for value in [b"private_user", "검증닉네임".encode(), b'"age": "30"']:
            self.assertNotIn(value, auth.users_path().read_bytes())
        with self.assertRaises(auth.privacy.StorageError):
            auth.current(other)

    def test_missing_key_fails_closed(self):
        token, _ = auth.signup(registration("keycheck"))
        path = auth.privacy.key_path(auth.users_path())
        original = path.read_bytes()
        path.unlink()
        try:
            with self.assertRaises(auth.privacy.StorageError):
                auth.current(token)
            self.assertFalse(path.exists())
        finally:
            path.write_bytes(original)
            path.chmod(0o600)

    def test_legacy_account_and_session_survive_encryption_migration(self):
        path = auth.users_path()
        password = auth.hash_password("legacy-password")
        with sqlite3.connect(path) as db:
            db.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,"
                "password_hash TEXT NOT NULL,profile TEXT,created_at INTEGER NOT NULL);"
                "CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id) "
                "ON DELETE CASCADE,expires_at INTEGER NOT NULL);")
            db.execute("INSERT INTO users VALUES(1,?,?,?,?)", ("legacy_user", password,
                json.dumps({"nickname": "이전닉네임", "allergy": "땅콩"}, ensure_ascii=False), int(time.time())))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (auth._token_hash("old-session"), 1,
                                                            int(time.time()) + 1000))
        report = migrate(Path(self.temp.name) / "backups", Path(self.temp.name) / "key-backups")
        self.assertEqual((report["users"], report["sessions"], report["integrity"]), (1, 1, "ok"))
        encrypted = Path(report["encrypted_backup"]).read_bytes()
        self.assertNotIn(b"legacy_user", encrypted)
        cipher, _ = auth.privacy.keys(path)
        self.assertTrue(cipher.decrypt(encrypted).startswith(b"SQLite format 3"))
        current = auth.current("old-session")
        self.assertEqual(current[0], 1)
        self.assertEqual(current[1]["profile"]["allergy"], "땅콩")
        self.assertEqual(auth.login({"username": "legacy_user", "password": "legacy-password"})[1]
                         ["user"]["username"], "l***")
        with auth.connect() as db:
            db.execute("VACUUM")
        self.assertNotIn(b"legacy_user", path.read_bytes())
        self.assertNotIn("이전닉네임".encode(), path.read_bytes())


class AuthHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PICKMENU_USERS_PATH": str(Path(self.temp.name) / "users.sqlite3"),
            "PICKMENU_REQUIRE_LOGIN": "1",
            "PICKMENU_ACCOUNT_KEY_PATH": str(Path(self.temp.name) / "accounts.key"),
        })
        self.env.start()
        os.environ.pop("PICKMENU_API_TOKEN", None)
        auth._failures.clear()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.env.stop()
        self.temp.cleanup()

    def call(self, method, path, body=None, cookie=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Cookie": cookie} if cookie else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read()), response.getheader("Set-Cookie")
        connection.close()
        return result

    def test_flow(self):
        self.assertEqual(self.call("GET", "/api/auth/me")[1], {"user": None, "profile": None})
        self.assertEqual(self.call("PUT", "/api/profile", {"diet": "vegan"})[0], 401)
        status, data, cookie = self.call("POST", "/api/auth/signup", registration("flow"))
        self.assertEqual((status, data["user"]["username"]), (201, "f***"))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        session = cookie.split(";")[0]
        self.assertTrue(session.startswith(f"{SESSION_COOKIE}="))
        status, data, _ = self.call("PUT", "/api/profile", {"diet": "vegan", "allergy": "땅콩"}, session)
        self.assertEqual((status, data["profile"]["diet"]), (200, "vegan"))
        self.assertEqual(self.call("GET", "/api/auth/me", cookie=session)[1]["profile"]["allergy"], "땅콩")
        self.assertEqual(self.call("POST", "/api/auth/login", {"username": "flow", "password": "nope-nope"})[0], 401)
        status, data, _ = self.call("POST", "/api/auth/login", {"username": "flow", "password": "password1"})
        self.assertEqual(data["profile"]["diet"], "vegan")
        self.assertEqual(self.call("DELETE", "/api/profile", cookie=session)[1], {"profile": None})
        status, _, cleared = self.call("POST", "/api/auth/logout", cookie=session)
        self.assertIn("Max-Age=0", cleared)
        self.assertEqual(self.call("GET", "/api/auth/me", cookie=session)[1]["user"], None)
        self.assertEqual(self.call("PUT", "/api/nothing", {})[0], 404)

    def test_recommendation_requires_live_login(self):
        menu = {"standard_menu_id": "MN_1", "name": "두부덮밥", "category": "밥·덮밥",
                "components": ["두부덮밥"], "spice": "", "soup": "no", "nutrition": None,
                "regions": {"수원시": {"reviews": 30, "stores": 3}}}
        with patch("server.catalog", return_value=[menu]):
            self.assertEqual(self.call("GET", "/api/catalog")[0], 200)
            self.assertEqual(self.call("POST", "/api/recommend", {})[0], 401)
            self.assertEqual(self.call("GET", "/api/menus/MN_1")[0], 401)
            self.assertEqual(self.call("POST", "/api/recommend", {}, "pickmenu_session=invalid")[0], 401)
            _, _, cookie = self.call("POST", "/api/auth/signup", registration("member"))
            session = cookie.split(";")[0]
            self.assertEqual(self.call("POST", "/api/recommend", {}, session)[0], 200)
            self.assertEqual(self.call("GET", "/api/menus/MN_1", cookie=session)[0], 200)
            self.call("POST", "/api/auth/logout", cookie=session)
            self.assertEqual(self.call("POST", "/api/recommend", {}, session)[0], 401)
            with patch.dict(os.environ, {"PICKMENU_REQUIRE_LOGIN": "0"}):
                self.assertEqual(self.call("POST", "/api/recommend", {})[0], 200)


if __name__ == "__main__":
    unittest.main()
