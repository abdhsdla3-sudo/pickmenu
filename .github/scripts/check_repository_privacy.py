"""Report tracked private paths and common credentials without printing their values."""

import re
import subprocess
from pathlib import Path

PRIVATE_FILES = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\.(?!example$|sample$).*)?|\.pgpass|\.pg_service\.conf|"
    r"pgpass\.conf|account-keys\.json|id_rsa|id_ed25519|id_ecdsa)$|"
    r"\.(?:sqlite[23]?|db|dump|key|pem|p12|pfx|fernet|gguf|safetensors)(?:$|-(?:wal|shm|journal)$)"
)
PATTERNS = {
    "local Windows path": re.compile(r"(?<![?\w])[A-Z]:[\\/]+(?!Program Files\b|Windows\b)[\w%]", re.IGNORECASE),
    "local account home": re.compile(r"/(?:home|Users)/[\w.-]+/", re.IGNORECASE),
    "private network access": re.compile(
        r"https://(?:login|console)\.tailscale\.com/uinv/[\w-]+|"
        r"\b[\w-]+\.tail[0-9a-z]+\.ts\.net\b",
        re.IGNORECASE,
    ),
    "private key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "API token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|"
        r"AIza[A-Za-z0-9_-]{35}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
        r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{28,}|tskey-(?:auth|api|client)-[A-Za-z0-9_-]{15,})\b"
    ),
    "database URL with password": re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^/\s:@]+:[^/\s@]+@",
        re.IGNORECASE,
    ),
}


def main() -> int:
    files = subprocess.check_output(["git", "ls-files", "-z"]).decode("utf-8").split("\0")
    failed = False
    for name in filter(None, files):
        if PRIVATE_FILES.search(name):
            print(f"{name}: private file type")
            failed = True
            continue
        path = Path(name)
        if path.is_symlink() or not path.is_file():
            continue
        contents = path.read_bytes()
        if contents.startswith((b"SQLite format 3\0", b"PGDMP")):
            print(f"{name}: database file")
            failed = True
            continue
        if b"\0" in contents and not contents.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        encoding = "utf-16" if contents.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
        for index, line in enumerate(contents.decode(encoding, errors="replace").splitlines(), 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    print(f"{name}:{index}: {label}")
                    failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
