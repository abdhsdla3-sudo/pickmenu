"""픽메뉴 회원 계정, 로그인 세션, 서버 저장 프로필."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from raw import LOCAL

USERS = LOCAL / "users.sqlite3"
SESSION_DAYS = 30
SESSION_SECONDS = SESSION_DAYS * 24 * 60 * 60
USERNAME = re.compile(r"[a-z0-9_]{4,20}")
PASSWORD_MIN, PASSWORD_MAX = 8, 64
# 서버에는 추천에 쓰는 취향과 닉네임만 저장한다. 신체정보는 브라우저에만 둔다.
PROFILE_FIELDS = {"nickname": 12, "diet": 20, "goal": 20, "spice": 10, "soup": 10,
                  "category": 40, "exclude": 100, "allergy": 100}
LOCK_FAILURES, LOCK_SECONDS = 5, 5 * 60
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

_failures: dict[str, tuple[int, float]] = {}
_failures_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    profile TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
"""


class AuthError(ValueError):
    """사용자에게 그대로 보여줄 수 있는 인증 오류."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def users_path() -> Path:
    configured = os.environ.get("PICKMENU_USERS_PATH")
    return Path(configured).expanduser().resolve() if configured else USERS


def connect() -> sqlite3.Connection:
    path = users_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    return db


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    encode = base64.b64encode
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${encode(salt).decode()}${encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def credentials(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise AuthError("아이디와 비밀번호를 입력해 주세요.")
    username, password = payload.get("username"), payload.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        raise AuthError("아이디와 비밀번호를 입력해 주세요.")
    return username.strip().lower(), password


def clean_profile(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise AuthError("프로필 형식을 확인해 주세요.")
    profile = {}
    for key, limit in PROFILE_FIELDS.items():
        item = value.get(key, "")
        if not isinstance(item, str) or len(item) > limit:
            raise AuthError("프로필 값의 형식 또는 길이를 확인해 주세요.")
        profile[key] = item.strip()
    return profile


def _new_session(db: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
               (_token_hash(token), user_id, now + SESSION_SECONDS))
    return token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _account(username: str, profile: str | None) -> dict[str, Any]:
    return {"user": {"username": username}, "profile": json.loads(profile) if profile else None}


def signup(payload: Any) -> tuple[str, dict[str, Any]]:
    username, password = credentials(payload)
    if not USERNAME.fullmatch(username):
        raise AuthError("아이디는 영문 소문자, 숫자, 밑줄(_)로 4~20자여야 해요.")
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise AuthError(f"비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자로 정해 주세요.")
    password_hash = hash_password(password)
    with closing(connect()) as db, db:
        try:
            cursor = db.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, int(time.time())))
        except sqlite3.IntegrityError:
            raise AuthError("이미 사용 중인 아이디예요.", 409) from None
        token = _new_session(db, int(cursor.lastrowid or 0))
    return token, _account(username, None)


def _check_lock(username: str) -> None:
    with _failures_lock:
        count, since = _failures.get(username, (0, 0.0))
        if count >= LOCK_FAILURES and time.monotonic() - since < LOCK_SECONDS:
            raise AuthError("로그인 시도가 많아요. 5분 뒤 다시 시도해 주세요.", 429)


def _record_failure(username: str) -> None:
    with _failures_lock:
        count, since = _failures.get(username, (0, 0.0))
        if time.monotonic() - since >= LOCK_SECONDS:
            count, since = 0, time.monotonic()
        _failures[username] = (count + 1, since)


def login(payload: Any) -> tuple[str, dict[str, Any]]:
    username, password = credentials(payload)
    _check_lock(username)
    with closing(connect()) as db, db:
        row = db.execute("SELECT id, password_hash, profile FROM users WHERE username = ?",
                         (username,)).fetchone()
        # 없는 아이디도 같은 시간이 걸리도록 비교용 해시를 계산한다.
        stored = row[1] if row else _DUMMY_HASH
        if not verify_password(password, stored) or row is None:
            _record_failure(username)
            raise AuthError("아이디 또는 비밀번호가 맞지 않아요.", 401)
        with _failures_lock:
            _failures.pop(username, None)
        token = _new_session(db, row[0])
    return token, _account(username, row[2])


def logout(token: str | None) -> None:
    if not token:
        return
    with closing(connect()) as db, db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def current(token: str | None) -> tuple[int, dict[str, Any]] | None:
    if not token:
        return None
    with closing(connect()) as db:
        row = db.execute(
            "SELECT users.id, users.username, users.profile FROM sessions "
            "JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
            (_token_hash(token), int(time.time()))).fetchone()
    return (row[0], _account(row[1], row[2])) if row else None


def save_profile(user_id: int, value: Any) -> dict[str, str] | None:
    profile = None if value is None else clean_profile(value)
    with closing(connect()) as db, db:
        db.execute("UPDATE users SET profile = ? WHERE id = ?",
                   (json.dumps(profile, ensure_ascii=False) if profile else None, user_id))
    return profile


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
