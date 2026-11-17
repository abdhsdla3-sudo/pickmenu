"""계정 DB의 검색용 HMAC, 표시 마스킹, 인증된 프로필 암호화."""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import tempfile
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_key_lock = threading.Lock()
PREFIX = "enc:v1:"


class StorageError(sqlite3.DatabaseError):
    """인증 정보나 암호문을 로그에 노출하지 않는 저장 오류."""


def key_path(database: Path) -> Path:
    configured = os.environ.get("PICKMENU_ACCOUNT_KEY_PATH")
    return Path(configured).expanduser().resolve() if configured else database.with_suffix(".key")


def keys(database: Path) -> tuple[Fernet, bytes]:
    path = key_path(database)
    try:
        with _key_lock:
            if not path.exists():
                # 암호화된 회원이 있는데 키가 사라졌으면 새 키를 만들지 않는다.
                if database.exists():
                    with closing(sqlite3.connect(database)) as db:
                        if db.execute("SELECT 1 FROM users WHERE username LIKE 'u1:%' LIMIT 1").fetchone():
                            raise StorageError("계정 암호화 키를 사용할 수 없습니다.")
                path.parent.mkdir(parents=True, exist_ok=True)
                value = {"encryption": Fernet.generate_key().decode(), "lookup": secrets.token_hex(32)}
                with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8",
                                                 delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump(value, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    temporary.chmod(0o600)
                    try:
                        os.link(temporary, path)
                    except FileExistsError:
                        pass
                finally:
                    temporary.unlink()
            if os.name == "posix" and path.stat().st_mode & 0o077:
                raise StorageError("계정 암호화 키 권한을 확인해야 합니다.")
            value = json.loads(path.read_text(encoding="utf-8"))
        lookup = bytes.fromhex(value["lookup"])
        if len(lookup) != 32:
            raise ValueError("invalid key")
        return Fernet(value["encryption"].encode()), lookup
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        raise StorageError("계정 암호화 키를 사용할 수 없습니다.") from None


def lookup(username: str, database: Path) -> str:
    _, secret = keys(database)
    return "u1:" + hmac.new(secret, username.encode(), hashlib.sha256).hexdigest()


def mask(value: str) -> str:
    return value[:1] + "***" if len(value) > 1 else "***"


def encrypt(profile: dict[str, str], owner: str, database: Path) -> str:
    cipher, _ = keys(database)
    raw = json.dumps({"owner": owner, "profile": profile}, ensure_ascii=False).encode()
    return PREFIX + cipher.encrypt(raw).decode()


def decrypt(value: str | None, owner: str, database: Path) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        if not value.startswith(PREFIX):
            raise ValueError("unencrypted profile")
        cipher, _ = keys(database)
        saved = json.loads(cipher.decrypt(value[len(PREFIX):].encode()))
        if not hmac.compare_digest(saved["owner"], owner) or not isinstance(saved["profile"], dict):
            raise ValueError("invalid owner")
        return dict(saved["profile"])
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise StorageError("저장된 계정 정보를 확인할 수 없습니다.") from None
