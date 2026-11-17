"""서비스를 중지한 상태에서 기존 계정을 암호화하고 비공개 복구본을 만든다."""

import argparse
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import account_crypto as privacy
import auth


def private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name == "posix" and path.parent.stat().st_mode & 0o077:
        raise privacy.StorageError("백업 폴더는 소유자 전용이어야 합니다.")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def migrate(backups: Path, key_backups: Path) -> dict[str, Any]:
    database = auth.users_path()
    if not database.is_file():
        raise ValueError("기존 회원 DB가 없습니다. 경로를 확인해 주세요.")
    cipher, _ = privacy.keys(database)
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(":memory:")) as memory:
        source.backup(memory)
        before = memory.execute("SELECT id,password_hash FROM users ORDER BY id").fetchall()
        sessions = memory.execute("SELECT * FROM sessions ORDER BY token_hash").fetchall()
        raw = memory.serialize()
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    backup = backups / f"users-before-private-{name}.sqlite.fernet"
    key_backup = key_backups / f"account-keys-{name}.key"
    private_write(backup, cipher.encrypt(raw))
    private_write(key_backup, privacy.key_path(database).read_bytes())
    with closing(auth.connect()) as db:
        if before != db.execute("SELECT id,password_hash FROM users ORDER BY id").fetchall():
            raise privacy.StorageError("이전 후 회원 정보 검증에 실패했습니다.")
        if sessions != db.execute("SELECT * FROM sessions ORDER BY token_hash").fetchall():
            raise privacy.StorageError("이전 후 세션 검증에 실패했습니다.")
        if db.execute("SELECT count(*) FROM users WHERE username NOT LIKE 'u1:%'").fetchone()[0]:
            raise privacy.StorageError("이전 후 아이디 보호 검증에 실패했습니다.")
        if db.execute("SELECT count(*) FROM users WHERE profile IS NOT NULL "
                      "AND profile NOT LIKE 'enc:v1:%'").fetchone()[0]:
            raise privacy.StorageError("이전 후 프로필 보호 검증에 실패했습니다.")
        db.execute("VACUUM")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise privacy.StorageError("회원 DB 무결성 검증에 실패했습니다.")
    return {"users": len(before), "sessions": len(sessions), "integrity": "ok",
            "encrypted_backup": str(backup), "private_key_backup": str(key_backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--key-backup-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.backup_dir, args.key_backup_dir), ensure_ascii=False, indent=2))
