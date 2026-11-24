"""픽메뉴 운영(Admin) 자료: 관리자 권한, 사용 기록, AI 호출 기록, 대화 검수, 피드백, 메뉴 관리와 변경 이력.

회원 DB·카탈로그와 다른 SQLite 파일(PICKMENU_ADMIN_DB_PATH, 기본 local/admin.sqlite3)에 둔다.
개인정보 원칙:
- 사용 기록에는 회원 ID를 남기지 않는다. 방문자는 브라우저 쿠키의 임의 값(방문자 ID)으로만 센다.
- 이야기 대화는 검수를 위해 CHAT_DAYS일만 보관하고 지난 것은 지운다. 회원 ID는 남기지 않는다.
- 관리자 권한은 VM에서 `python admin.py grant 아이디`로만 준다(가입만으로 관리자가 될 수 없다).
"""

import argparse
import json
import os
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from raw import LOCAL

ADMIN_DB = LOCAL / "admin.sqlite3"
CHAT_DAYS = 14
EVENT_DAYS = 180
KST = timezone(timedelta(hours=9))
EVENT_KINDS = {"visit", "recommend", "chat", "menu_click", "store_click", "store_map"}
CLIENT_EVENTS = {"visit", "menu_click", "store_click", "store_map"}
FEEDBACK_KINDS = {"bad_recommendation": "추천이 별로예요", "error": "오류 신고", "idea": "의견"}
FEEDBACK_STATES = {"new": "접수", "in_progress": "처리 중", "done": "완료"}
REVIEW_STATES = {"unchecked": "미검수", "correct": "맞음", "wrong": "틀림"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, label TEXT NOT NULL, granted_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, at INTEGER NOT NULL, kind TEXT NOT NULL, visitor TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE TABLE IF NOT EXISTS ai_calls (
    id INTEGER PRIMARY KEY, at INTEGER NOT NULL, purpose TEXT NOT NULL, model TEXT NOT NULL, ms INTEGER NOT NULL,
    prompt_tokens INTEGER, output_tokens INTEGER, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ai_calls_at ON ai_calls(at);
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY, at INTEGER NOT NULL, messages TEXT NOT NULL, ai_output TEXT, conditions TEXT NOT NULL,
    clarified INTEGER NOT NULL DEFAULT 0, menus TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'unchecked',
    note TEXT NOT NULL DEFAULT '', reviewed_at INTEGER, reviewer TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS chats_at ON chats(at);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY, at INTEGER NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, context TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new', note TEXT NOT NULL DEFAULT '', updated_at INTEGER, updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS menu_overrides (
    menu_id TEXT PRIMARY KEY, hidden INTEGER NOT NULL DEFAULT 0, display_name TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL, updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, at INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL,
    before TEXT NOT NULL DEFAULT '', after TEXT NOT NULL DEFAULT ''
);
"""
_ready: set[str] = set()
_ready_lock = threading.Lock()
_overrides: dict[str, Any] = {"version": -1, "value": {}}
_overrides_lock = threading.Lock()
_version = [0]


def db_path() -> Path:
    configured = os.environ.get("PICKMENU_ADMIN_DB_PATH")
    return Path(configured).expanduser().resolve() if configured else ADMIN_DB


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix" and not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    with closing(sqlite3.connect(path, timeout=10)) as db:
        if str(path) not in _ready:
            with _ready_lock:
                db.executescript(SCHEMA)
                _ready.add(str(path))
        with db:
            yield db


def now() -> int:
    return int(time.time())


def clip(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


# ---------- 권한 ----------

def is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    try:
        with connect() as db:
            return db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone() is not None
    except sqlite3.Error:
        return False


def grant(user_id: int, label: str) -> None:
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO admins VALUES (?,?,?)", (user_id, label, now()))
        db.execute("INSERT INTO audit (at,actor,action,target,after) VALUES (?,?,?,?,?)",
                   (now(), "VM 관리자", "관리자 권한 부여", label, ""))


def revoke(user_id: int, label: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        db.execute("INSERT INTO audit (at,actor,action,target) VALUES (?,?,?,?)",
                   (now(), "VM 관리자", "관리자 권한 회수", label))


# ---------- 기록 ----------

def record_event(kind: str, visitor: str, detail: str = "") -> None:
    if kind not in EVENT_KINDS:
        return
    try:
        with connect() as db:
            db.execute("INSERT INTO events (at,kind,visitor,detail) VALUES (?,?,?,?)",
                       (now(), kind, clip(visitor, 40) or "-", clip(detail, 80)))
    except sqlite3.Error:
        pass  # 기록 실패가 추천을 막지 않는다.


def record_ai_call(purpose: str, model: str, ms: int, prompt_tokens: int | None, output_tokens: int | None,
                   status: str, error: str = "") -> None:
    try:
        with connect() as db:
            db.execute("INSERT INTO ai_calls (at,purpose,model,ms,prompt_tokens,output_tokens,status,error) "
                       "VALUES (?,?,?,?,?,?,?,?)",
                       (now(), purpose, clip(model, 60), ms, prompt_tokens, output_tokens, status, clip(error, 120)))
    except sqlite3.Error:
        pass


def record_chat(messages: list[str], ai_output: dict[str, Any] | None, conditions: dict[str, Any],
                menus: list[str], clarified: bool) -> None:
    try:
        with connect() as db:
            db.execute("DELETE FROM chats WHERE at < ?", (now() - CHAT_DAYS * 86400,))
            db.execute("INSERT INTO chats (at,messages,ai_output,conditions,clarified,menus) VALUES (?,?,?,?,?,?)",
                       (now(), json.dumps(messages, ensure_ascii=False),
                        json.dumps(ai_output, ensure_ascii=False) if ai_output is not None else None,
                        json.dumps(conditions, ensure_ascii=False), int(clarified),
                        json.dumps(menus[:8], ensure_ascii=False)))
    except sqlite3.Error:
        pass


def add_feedback(payload: Any) -> int:
    if not isinstance(payload, dict):
        raise ValueError("의견 형식을 확인해 주세요.")
    kind = payload.get("kind")
    message = clip(payload.get("message"), 300)
    if kind not in FEEDBACK_KINDS:
        raise ValueError("의견 종류를 골라 주세요.")
    if kind != "bad_recommendation" and len(message) < 2:
        raise ValueError("내용을 2자 이상 적어 주세요.")
    given = payload.get("context")
    raw: dict[str, Any] = given if isinstance(given, dict) else {}
    context = {
        "mode": clip(raw.get("mode"), 10),
        "menus": [clip(name, 40) for name in raw.get("menus", []) if isinstance(name, str)][:8]
        if isinstance(raw.get("menus"), list) else [],
        "conditions": clip(raw.get("conditions"), 200),
    }
    with connect() as db:
        cursor = db.execute("INSERT INTO feedback (at,kind,message,context) VALUES (?,?,?,?)",
                            (now(), kind, message, json.dumps(context, ensure_ascii=False)))
        return int(cursor.lastrowid or 0)


# ---------- 메뉴 관리 ----------

def overrides() -> dict[str, dict[str, Any]]:
    """메뉴 ID → {"hidden", "display_name"}. 바뀌기 전까지 캐시한다."""
    with _overrides_lock:
        if _overrides["version"] == _version[0]:
            return _overrides["value"]
    try:
        with connect() as db:
            value = {menu_id: {"hidden": bool(hidden), "display_name": name}
                     for menu_id, hidden, name in db.execute(
                         "SELECT menu_id,hidden,display_name FROM menu_overrides WHERE hidden=1 OR display_name!=''")}
    except sqlite3.Error:
        return {}
    with _overrides_lock:
        _overrides.update(version=_version[0], value=value)
    return value


def apply_overrides(menus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """숨긴 메뉴를 빼고 표시 이름을 바꾼다. 원본 목록과 메뉴는 고치지 않는다."""
    changes = overrides()
    if not changes:
        return menus
    result = []
    for menu in menus:
        change = changes.get(menu["standard_menu_id"])
        if change is None:
            result.append(menu)
        elif not change["hidden"]:
            result.append({**menu, "name": change["display_name"] or menu["name"]})
    return result


def update_menu(menu_id: str, payload: Any, actor: str, original_name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("입력 형식을 확인해 주세요.")
    hidden = payload.get("hidden")
    if not isinstance(hidden, bool):
        raise ValueError("숨김 여부를 확인해 주세요.")
    name = clip(payload.get("display_name"), 60)
    note = clip(payload.get("note"), 200)
    with connect() as db:
        row = db.execute("SELECT hidden,display_name,note FROM menu_overrides WHERE menu_id=?", (menu_id,)).fetchone()
        before = {"hidden": bool(row[0]), "display_name": row[1], "note": row[2]} if row else \
            {"hidden": False, "display_name": "", "note": ""}
        after = {"hidden": hidden, "display_name": name, "note": note}
        db.execute("INSERT OR REPLACE INTO menu_overrides VALUES (?,?,?,?,?,?)",
                   (menu_id, int(hidden), name, note, now(), actor))
        db.execute("INSERT INTO audit (at,actor,action,target,before,after) VALUES (?,?,?,?,?,?)",
                   (now(), actor, "메뉴 수정", f"{menu_id} {original_name}",
                    json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
    with _overrides_lock:
        _version[0] += 1
    return after


def menu_rows(menus: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """관리 화면 메뉴 검색. 원본 카탈로그(숨김 적용 전)를 받는다."""
    try:
        with connect() as db:
            saved = {row[0]: row[1:] for row in db.execute(
                "SELECT menu_id,hidden,display_name,note,updated_at,updated_by FROM menu_overrides")}
    except sqlite3.Error:
        saved = {}
    query = query.strip()
    found = []
    for menu in menus:
        food = (menu.get("components") or [""])[0]
        if query and query not in menu["name"] and query not in food and query != menu["standard_menu_id"]:
            continue
        if not query and menu["standard_menu_id"] not in saved:
            continue
        hidden, display, note, updated_at, updated_by = saved.get(menu["standard_menu_id"], (0, "", "", None, ""))
        found.append({"standard_menu_id": menu["standard_menu_id"], "name": menu["name"], "food": food,
                      "category": menu["category"], "nutrition_status": menu.get("nutrition_status", ""),
                      "hidden": bool(hidden), "display_name": display, "note": note,
                      "updated_at": updated_at, "updated_by": updated_by})
        if len(found) >= 50:
            break
    return found


# ---------- 관리 화면 조회 ----------

def day(ts: int) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%m-%d")


def summary(days: int = 14) -> dict[str, Any]:
    days = max(1, min(days, 90))
    start = now() - days * 86400
    with connect() as db:
        db.execute("DELETE FROM events WHERE at < ?", (now() - EVENT_DAYS * 86400,))
        events = db.execute("SELECT at,kind,visitor,detail FROM events WHERE at >= ?", (start,)).fetchall()
        first_seen = dict(db.execute("SELECT visitor, MIN(at) FROM events WHERE kind='visit' GROUP BY visitor"))
        calls = db.execute("SELECT at,purpose,ms,prompt_tokens,output_tokens,status FROM ai_calls WHERE at >= ?",
                           (start,)).fetchall()
        feedback = Counter(status for status, in db.execute("SELECT status FROM feedback"))
        reviews = Counter(status for status, in db.execute("SELECT status FROM chats"))
    labels = [day(now() - offset * 86400) for offset in range(days - 1, -1, -1)]
    daily: dict[str, dict[str, Any]] = {label: {"day": label, "visitors": set(), "recommend": 0, "chat": 0,
                                                "menu_click": 0, "store_click": 0} for label in labels}
    visitors: set[str] = set()
    returning: set[str] = set()
    visit_days: dict[str, set[str]] = {}
    clicks: Counter[str] = Counter()
    searches: Counter[str] = Counter()
    for at, kind, visitor, detail in events:
        label = day(at)
        if label not in daily:
            continue
        row = daily[label]
        if kind == "visit":
            row["visitors"].add(visitor)
            visitors.add(visitor)
            visit_days.setdefault(visitor, set()).add(label)
            if first_seen.get(visitor, at) < at - 3600 * 6 and day(first_seen[visitor]) != label:
                returning.add(visitor)
        elif kind in row:
            row[kind] += 1
        if kind == "menu_click" and detail:
            clicks[detail] += 1
        if kind in {"recommend", "chat"} and detail:
            for word in detail.split("|"):
                if word:
                    searches[word] += 1
    for visitor, seen in visit_days.items():
        if len(seen) > 1:
            returning.add(visitor)
    timings = sorted(ms for _, _, ms, _, _, status in calls if status == "ok")
    ok = [call for call in calls if call[5] == "ok"]
    return {
        "days": days,
        "daily": [{**row, "visitors": len(row["visitors"])} for row in daily.values()],
        "totals": {
            "visitors": len(visitors), "returning": len(returning),
            "recommend": sum(row["recommend"] for row in daily.values()),
            "chat": sum(row["chat"] for row in daily.values()),
            "menu_click": sum(row["menu_click"] for row in daily.values()),
            "store_click": sum(row["store_click"] for row in daily.values()),
        },
        "top_menus": clicks.most_common(10),
        "top_searches": searches.most_common(10),
        "ai": {
            "calls": len(calls), "ok": len(ok),
            "errors": sum(1 for call in calls if call[5] != "ok"),
            "avg_ms": round(sum(timings) / len(timings)) if timings else None,
            "p95_ms": timings[min(len(timings) - 1, int(len(timings) * 0.95))] if timings else None,
            "prompt_tokens": sum(call[3] or 0 for call in calls),
            "output_tokens": sum(call[4] or 0 for call in calls),
        },
        "feedback": {state: feedback.get(state, 0) for state in FEEDBACK_STATES},
        "reviews": {state: reviews.get(state, 0) for state in REVIEW_STATES},
    }


def chats(status: str = "", limit: int = 50) -> list[dict[str, Any]]:
    with connect() as db:
        db.execute("DELETE FROM chats WHERE at < ?", (now() - CHAT_DAYS * 86400,))
        sql = "SELECT id,at,messages,ai_output,conditions,clarified,menus,status,note,reviewer FROM chats"
        args: tuple[Any, ...] = ()
        if status in REVIEW_STATES:
            sql += " WHERE status=?"
            args = (status,)
        rows = db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, max(1, min(limit, 200)))).fetchall()
    return [{"id": r[0], "at": r[1], "messages": json.loads(r[2]),
             "ai_output": json.loads(r[3]) if r[3] else None, "conditions": json.loads(r[4]),
             "clarified": bool(r[5]), "menus": json.loads(r[6]), "status": r[7], "note": r[8], "reviewer": r[9]}
            for r in rows]


def review_chat(chat_id: int, payload: Any, actor: str) -> None:
    if not isinstance(payload, dict) or payload.get("status") not in REVIEW_STATES:
        raise ValueError("검수 결과를 골라 주세요.")
    note = clip(payload.get("note"), 300)
    with connect() as db:
        row = db.execute("SELECT status,note FROM chats WHERE id=?", (chat_id,)).fetchone()
        if row is None:
            raise LookupError("대화를 찾을 수 없어요.")
        db.execute("UPDATE chats SET status=?,note=?,reviewed_at=?,reviewer=? WHERE id=?",
                   (payload["status"], note, now(), actor, chat_id))
        db.execute("INSERT INTO audit (at,actor,action,target,before,after) VALUES (?,?,?,?,?,?)",
                   (now(), actor, "AI 결과 검수", f"대화 #{chat_id}",
                    json.dumps({"status": row[0], "note": row[1]}, ensure_ascii=False),
                    json.dumps({"status": payload["status"], "note": note}, ensure_ascii=False)))


def feedback_rows(status: str = "") -> list[dict[str, Any]]:
    with connect() as db:
        sql = "SELECT id,at,kind,message,context,status,note,updated_at,updated_by FROM feedback"
        args: tuple[Any, ...] = ()
        if status in FEEDBACK_STATES:
            sql += " WHERE status=?"
            args = (status,)
        rows = db.execute(sql + " ORDER BY id DESC LIMIT 200", args).fetchall()
    return [{"id": r[0], "at": r[1], "kind": r[2], "message": r[3], "context": json.loads(r[4]), "status": r[5],
             "note": r[6], "updated_at": r[7], "updated_by": r[8]} for r in rows]


def update_feedback(feedback_id: int, payload: Any, actor: str) -> None:
    if not isinstance(payload, dict) or payload.get("status") not in FEEDBACK_STATES:
        raise ValueError("처리 상태를 골라 주세요.")
    note = clip(payload.get("note"), 300)
    with connect() as db:
        row = db.execute("SELECT status,note FROM feedback WHERE id=?", (feedback_id,)).fetchone()
        if row is None:
            raise LookupError("의견을 찾을 수 없어요.")
        db.execute("UPDATE feedback SET status=?,note=?,updated_at=?,updated_by=? WHERE id=?",
                   (payload["status"], note, now(), actor, feedback_id))
        db.execute("INSERT INTO audit (at,actor,action,target,before,after) VALUES (?,?,?,?,?,?)",
                   (now(), actor, "피드백 처리", f"의견 #{feedback_id}",
                    json.dumps({"status": row[0], "note": row[1]}, ensure_ascii=False),
                    json.dumps({"status": payload["status"], "note": note}, ensure_ascii=False)))


def ai_calls(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT at,purpose,model,ms,prompt_tokens,output_tokens,status,error FROM ai_calls "
                          "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    return [{"at": r[0], "purpose": r[1], "model": r[2], "ms": r[3], "prompt_tokens": r[4],
             "output_tokens": r[5], "status": r[6], "error": r[7]} for r in rows]


def audit_rows(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT at,actor,action,target,before,after FROM audit ORDER BY id DESC LIMIT ?",
                          (max(1, min(limit, 500)),)).fetchall()
    return [{"at": r[0], "actor": r[1], "action": r[2], "target": r[3], "before": r[4], "after": r[5]}
            for r in rows]


def main() -> None:
    """VM에서 관리자 권한을 준다/회수한다. 가입된 아이디여야 한다."""
    import account_crypto as privacy
    import auth  # 회원 DB 설정(PICKMENU_USERS_PATH)을 그대로 쓴다.

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("action", choices=["grant", "revoke", "list"])
    parser.add_argument("username", nargs="?")
    args = parser.parse_args()
    if args.action == "list":
        with connect() as db:
            for user_id, label, granted in db.execute("SELECT * FROM admins"):
                print(user_id, label, datetime.fromtimestamp(granted, KST).strftime("%Y-%m-%d %H:%M"))
        return
    if not args.username:
        parser.error("아이디를 적어 주세요.")
    with closing(auth.connect()) as users:
        row = users.execute("SELECT id, username_masked FROM users WHERE username=?",
                            (privacy.lookup(args.username, auth.users_path()),)).fetchone()
    if row is None:
        raise SystemExit("가입된 아이디가 아니에요. 사이트에서 먼저 회원가입해 주세요.")
    (grant if args.action == "grant" else revoke)(int(row[0]), row[1])
    print(f"{row[1]}: {'관리자 권한을 줬어요' if args.action == 'grant' else '관리자 권한을 회수했어요'}")


if __name__ == "__main__":
    main()
