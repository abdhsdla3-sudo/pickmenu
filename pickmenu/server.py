"""픽메뉴 로컬 HTTP 서버."""

import argparse
import hmac
import json
import os
import re
import sqlite3
import sys
import threading
from collections.abc import Iterable
from contextlib import closing
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import auth
import llm
import menu_reco
import menu_stores
import naver_maps
from raw import BASE, LOCAL
from recommend import DISTRICTS, Profile, apply_reference, recommend, reference_tags

CATALOG = LOCAL / "catalog.sqlite3"
MENU_ID = re.compile(r"MN_[0-9a-f]{16}")
SESSION_COOKIE = "pickmenu_session"
INVALID = object()
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/mark.svg": ("mark.svg", "image/svg+xml"),
}
ASSETS.update({f"/menu-photos/{path.name}": (f"menu-photos/{path.name}", "image/webp")
               for path in (BASE / "web" / "menu-photos").glob("[0-9][0-9][0-9].webp")})
PHOTO_CACHE = "public, max-age=86400"
REVIEW_PERIOD = os.environ.get("PICKMENU_REVIEW_PERIOD") or (BASE / "review_period.txt").read_text(encoding="utf-8").strip()
_loaded: dict[str, Any] = {"key": None, "reference": None, "menus": None}
_loaded_lock = threading.Lock()


def catalog_path() -> Path:
    configured = os.environ.get("PICKMENU_CATALOG_PATH")
    return Path(configured).expanduser().resolve() if configured else CATALOG


def catalog(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        path = catalog_path()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return [json.loads(value) for value, in db.execute("SELECT data FROM catalog ORDER BY id")]


def prepared_menus() -> list[dict[str, Any]]:
    """참고자료를 붙인 카탈로그. 카탈로그 파일과 참고자료가 바뀌지 않았으면 이전 결과를 쓴다.

    돌려준 목록과 메뉴 딕셔너리는 여러 요청이 함께 쓰므로 고치지 않는다(recommend는 복사해 쓴다).
    """
    path = catalog_path()
    reference = menu_reco.reference_data()
    try:
        key: tuple[str, int] | None = (str(path), path.stat().st_mtime_ns)
    except OSError:
        key = None  # 파일을 확인할 수 없으면 캐시하지 않고 catalog()가 오류를 알리게 한다.
    with _loaded_lock:
        if key is not None and _loaded["key"] == key and _loaded["reference"] is reference:
            return _loaded["menus"]
    menus = catalog(path)
    if not menus:
        raise ValueError("빈 카탈로그")
    prepared = apply_reference(menus, reference_tags(reference.columns, reference.menus) if reference else None)
    if key is not None:
        with _loaded_lock:
            _loaded.update(key=key, reference=reference, menus=prepared)
    return prepared


def user_location(payload: Any) -> tuple[float, float] | None:
    """지도에서 고른 위치 {"lat", "lng"}. 없으면 None, 형식·범위(대한민국)가 틀리면 ValueError."""
    value = payload.get("location") if isinstance(payload, dict) else None
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("위치 형식을 확인해 주세요.")
    lat, lng = value.get("lat"), value.get("lng")
    if (not isinstance(lat, (int, float)) or not isinstance(lng, (int, float))
            or isinstance(lat, bool) or isinstance(lng, bool)
            or not (33 <= lat <= 39 and 124 <= lng <= 132)):
        raise ValueError("지도에서 국내 위치를 골라 주세요.")
    return float(lat), float(lng)


def recent_foods(payload: Any) -> list[str]:
    """브라우저가 보낸 최근 추천 음식 이름(최근 3번 × 5개). 형식이 틀리면 ValueError."""
    value = payload.get("recent", []) if isinstance(payload, dict) else []
    if (not isinstance(value, list) or len(value) > 30
            or not all(isinstance(name, str) and 0 < len(name) <= 40 for name in value)):
        raise ValueError("최근 추천 목록 형식을 확인해 주세요.")
    return value


def categories(menus: list[dict[str, Any]]) -> list[str]:
    return sorted({menu["category"] for menu in menus})


def public_menu(menu: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in menu.items() if key not in {"regions", "mapping_note"}}


def session_cookie(token: str | None) -> str:
    """로그인 세션 쿠키. 토큰이 없으면 쿠키를 지운다."""
    parts = [f"{SESSION_COOKIE}={token or ''}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={auth.SESSION_SECONDS if token else 0}"]
    if os.environ.get("PICKMENU_COOKIE_SECURE") == "1":
        parts.append("Secure")
    return "; ".join(parts)


class Handler(BaseHTTPRequestHandler):
    timeout = 30  # 느린 연결(slowloris)이 스레드를 오래 붙잡지 않게 한다.

    def reply(self, status: int, kind: str, body: bytes,
              headers: Iterable[tuple[str, str]] = (), cache: str = "no-store") -> None:
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
                         "style-src 'self' https://cdnjs.cloudflare.com; "
                         "img-src 'self' data: https://cdnjs.cloudflare.com https://tile.openstreetmap.org; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, value: Any,
                  headers: Iterable[tuple[str, str]] = ()) -> None:
        self.reply(status, "application/json; charset=utf-8",
                   json.dumps(value, ensure_ascii=False).encode("utf-8"), headers)

    def read_json(self, maximum: int = 4096) -> Any:
        """JSON 본문을 읽는다. 실패하면 오류 응답을 보내고 INVALID를 돌려준다."""
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "입력 형식을 확인해 주세요."})
            return INVALID
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= maximum:
                raise ValueError
            return json.loads(self.rfile.read(size).decode("utf-8"))
        except ValueError:
            self.send_json(400, {"error": "입력 형식 또는 길이를 확인해 주세요."})
            return INVALID

    def reference(self) -> menu_reco.Data | None:
        """추천·지도 참고자료 DB를 읽는다. 연결할 수 없으면 503을 보낸다."""
        try:
            return menu_reco.data()
        except Exception as error:  # 드라이버 미설치·연결 실패·권한 오류를 같은 안내로 돌려준다.
            self.log_error("참고자료 DB 오류 %s", type(error).__name__)
            self.send_json(503, {"error": "가게 추천 자료를 불러올 수 없어요. 잠시 뒤 다시 시도해 주세요."})
            return None

    def session_token(self) -> str | None:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel and morsel.value else None

    def handle_auth(self, path: str) -> None:
        """회원가입, 로그인, 로그아웃과 서버 저장 프로필 요청을 처리한다."""
        try:
            if path in {"/api/auth/signup", "/api/auth/login"}:
                if (payload := self.read_json()) is INVALID:
                    return
                action = auth.signup if path.endswith("signup") else auth.login
                with auth.credential_slot():
                    token, account = action(payload)
                self.send_json(201 if path.endswith("signup") else 200, account,
                               [("Set-Cookie", session_cookie(token))])
            elif path == "/api/auth/logout":
                auth.logout(self.session_token())
                self.send_json(200, {"user": None}, [("Set-Cookie", session_cookie(None))])
            elif path == "/api/auth/me":
                found = auth.current(self.session_token())
                self.send_json(200, found[1] if found else {"user": None, "profile": None})
            elif path == "/api/profile":
                if (found := auth.current(self.session_token())) is None:
                    self.send_json(401, {"error": "로그인이 필요해요."})
                    return
                if self.command == "DELETE":
                    payload = None
                elif (payload := self.read_json()) is INVALID:
                    return
                self.send_json(200, {"profile": auth.save_profile(found[0], payload)})
        except auth.AuthError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.Error as error:
            self.log_error("회원 DB 오류 %s", type(error).__name__)
            self.send_json(503, {"error": "계정 정보를 처리할 수 없어요. 잠시 뒤 다시 시도해 주세요."})

    def authorize_api(self) -> bool:
        token = os.environ.get("PICKMENU_API_TOKEN")
        if not token:
            return True
        authorization = self.headers.get("Authorization", "").encode("utf-8")
        if hmac.compare_digest(authorization, f"Bearer {token}".encode("utf-8")):
            return True
        self.send_json(401, {"error": "API 인증이 필요해요."})
        return False

    def load(self) -> list[dict[str, Any]] | None:
        try:
            return prepared_menus()
        except (OSError, sqlite3.Error, ValueError) as error:
            self.log_error("카탈로그 오류 %s", type(error).__name__)
            self.send_json(503, {"error": "추천 데이터를 열 수 없어요. 전처리를 완료한 뒤 다시 시도해 주세요."})
            return None

    def require_session(self) -> bool:
        if os.environ.get("PICKMENU_REQUIRE_LOGIN") != "1":
            return True
        try:
            if auth.session_user_id(self.session_token()) is not None:
                return True
        except sqlite3.Error:
            self.send_json(503, {"error": "로그인 상태를 확인할 수 없어요. 잠시 뒤 다시 시도해 주세요."})
            return False
        self.send_json(401, {"error": "로그인하고 메뉴를 추천받아 주세요."})
        return False

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/") and not self.authorize_api():
            return
        if path in ASSETS:
            filename, content_type = ASSETS[path]
            self.reply(200, content_type, (BASE / "web" / filename).read_bytes(),
                       cache=PHOTO_CACHE if content_type == "image/webp" else "no-store")
        elif path == "/api/auth/me":
            self.handle_auth(path)
        elif path == "/api/v2/stores":
            if not self.require_session():
                return
            params = parse_qs(urlsplit(self.path).query)
            foods = [f for f in params.get("food", []) if f.strip()][:5]
            district = (params.get("district") or [""])[0]
            if not foods or any(len(f) > 40 for f in foods) or district not in ("", *menu_reco.DISTRICTS):
                self.send_json(400, {"error": "가게를 찾을 메뉴 이름과 지역을 확인해 주세요."})
            elif (data := self.reference()) is not None:
                self.send_json(200, menu_reco.stores_for_foods(data, foods, district))
        elif path == "/api/location/status":
            if self.require_session():
                self.send_json(200, {"address_search": naver_maps.available()})
        elif path == "/api/v2/options":
            if not self.require_session():
                return
            if (data := self.reference()) is not None:
                self.send_json(200, {"districts": list(menu_reco.DISTRICTS),
                                     "groups": menu_reco.public_groups(data), "batch_id": data.batch_id})
        elif path == "/api/catalog":
            if (menus := self.load()) is not None:
                self.send_json(200, {"categories": categories(menus), "menu_count": len(menus),
                                     "period": REVIEW_PERIOD})
        elif path.startswith("/api/menus/"):
            if not self.require_session():
                return
            if (menus := self.load()) is not None:
                mid = path.removeprefix("/api/menus/")
                selected = next((menu for menu in menus if menu["standard_menu_id"] == mid), None)
                if selected:
                    self.send_json(200, public_menu(selected))
                else:
                    self.send_json(404, {"error": "메뉴를 찾을 수 없어요."})
        else:
            self.send_json(404, {"error": "페이지를 찾을 수 없어요."})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path in {"/api/location/search", "/api/location/address"}:
            if not self.authorize_api() or not self.require_session():
                return
            if (payload := self.read_json()) is not INVALID:
                self.location_request(path, payload)
            return
        if path == "/api/chat":
            if not self.authorize_api() or not self.require_session():
                return
            if (payload := self.read_json(16384)) is INVALID:
                return
            if (menus := self.load()) is None:
                return
            try:
                profile, _ = llm.conditions(payload, categories(menus))
                result = self.recommendation(menus, profile)
                self.send_json(200, {"reply": llm.reply(result), "result": result,
                                     "conditions": vars(profile)})
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            except llm.Unavailable as error:
                self.send_json(503, {"error": str(error)})
            return
        if path in {"/api/auth/signup", "/api/auth/login", "/api/auth/logout"}:
            if self.authorize_api():
                self.handle_auth(path)
            return
        if path == "/api/v2/recommend":
            if not self.authorize_api() or not self.require_session():
                return
            if (payload := self.read_json()) is INVALID:
                return
            if (data := self.reference()) is None:
                return
            try:
                query = menu_reco.Query.parse(payload, set(data.visible_groups),
                                              set(data.visible_groups) | set(data.group_columns))
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            self.send_json(200, menu_reco.recommend(data, query))
            return
        if path not in {"/api/recommend", "/api/recommend/stores"}:
            self.send_json(404, {"error": "페이지를 찾을 수 없어요."})
            return
        if not self.authorize_api():
            return
        if not self.require_session():
            return
        if (payload := self.read_json()) is INVALID:
            return
        if (menus := self.load()) is None:
            return
        if path == "/api/recommend/stores":
            self.menu_stores(payload, menus)
            return
        try:
            profile = Profile.parse(payload, categories(menus))
            recent = recent_foods(payload)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return
        self.send_json(200, self.recommendation(menus, profile, recent))

    def recommendation(self, menus: list[dict[str, Any]], profile: Profile,
                       recent: list[str] | None = None) -> dict[str, Any]:
        result = recommend(menus, profile, recent or [])
        result["items"] = [public_menu(menu) for menu in result["items"]]
        result["families"] = [self.family(family, profile.district) for family in result["families"]]
        return result

    def family(self, family: dict[str, Any], district: str) -> dict[str, Any]:
        """대표 메뉴에 중복 없는 리뷰·매장 수를 붙인다. trace가 없으면 null로 둔다."""
        ids = [member["standard_menu_id"] for member in family["members"]]
        try:
            evidence: dict[str, int] | None = menu_stores.evidence(catalog_path(), ids, district)
        except sqlite3.Error as error:
            self.log_error("대표 메뉴 집계 오류 %s", type(error).__name__)
            evidence = None
        return {**family, "best": public_menu(family["best"]), "evidence": evidence}

    def menu_stores(self, payload: Any, menus: list[dict[str, Any]]) -> None:
        """2단계: 고른 대표 메뉴의 실제 메뉴 이름과 판매 매장을 돌려준다."""
        ids = payload.get("menu_ids") if isinstance(payload, dict) else None
        district = payload.get("district", "") if isinstance(payload, dict) else None
        if (not isinstance(ids, list) or not 0 < len(ids) <= 30 or district not in DISTRICTS
                or not all(isinstance(mid, str) and MENU_ID.fullmatch(mid) for mid in ids)):
            self.send_json(400, {"error": "메뉴와 지역을 확인해 주세요."})
            return
        try:
            origin = user_location(payload)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return
        by_id = {menu["standard_menu_id"]: menu for menu in menus}
        selected = [by_id[mid] for mid in dict.fromkeys(ids) if mid in by_id]
        if not selected:
            self.send_json(404, {"error": "메뉴를 찾을 수 없어요."})
            return
        try:
            result = menu_stores.stores(catalog_path(), selected, district, origin=origin)
            llm.store_comments(result)
            self.send_json(200, result)
        except sqlite3.Error as error:
            self.log_error("매장 집계 오류 %s", type(error).__name__)
            self.send_json(503, {"error": "매장 자료를 열 수 없어요. 잠시 뒤 다시 시도해 주세요."})

    def location_request(self, path: str, payload: Any) -> None:
        """주소와 좌표는 JSON 본문으로 받고 저장하거나 로그에 남기지 않는다."""
        try:
            if path == "/api/location/search":
                value = payload.get("query") if isinstance(payload, dict) else None
                query = value.strip() if isinstance(value, str) else ""
                if not 2 <= len(query) <= 80 or any(ord(c) < 32 for c in query):
                    self.send_json(400, {"error": "찾을 주소를 2~80자로 입력해 주세요."})
                    return
                self.send_json(200, {"results": naver_maps.geocode(query)})
            else:
                origin = user_location({"location": payload})
                if origin is None:
                    raise ValueError("위치를 확인해 주세요.")
                self.send_json(200, {"address": naver_maps.reverse(*origin)})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except naver_maps.Unavailable as error:
            self.send_json(503, {"error": str(error)})

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # URL의 임의 식별자와 질의, 본문, 클라이언트 주소는 기록하지 않는다.
        path = getattr(self, "path", "").split("?", 1)[0]
        if path in {
            "/", "/api/catalog", "/api/recommend", "/api/recommend/stores", "/api/chat",
            "/api/auth/signup", "/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/profile",
            "/api/location/status", "/api/location/search", "/api/location/address",
            "/api/v2/options", "/api/v2/recommend", "/api/v2/stores",
        }:
            route = path
        elif path.startswith("/api/menus/"):
            route = "/api/menus/:id"
        elif path in ASSETS:
            route = "asset"
        else:
            route = "other"
        method = self.command if self.command in {"GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"} else "-"
        self.log_message("%s %s %s", method, route, code)

    def log_error(self, format: str, *args: Any) -> None:
        if format == "code %d, message %s":
            self.log_message("HTTP 요청 오류 %s", args[0])
        elif format.startswith("Request timed out"):
            self.log_message("HTTP 요청 시간 초과")
        else:
            self.log_message(format, *args)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}", file=sys.stderr)

    def do_PUT(self) -> None:
        self.profile_request()

    def do_DELETE(self) -> None:
        self.profile_request()

    def profile_request(self) -> None:
        path = urlsplit(self.path).path
        if path != "/api/profile":
            self.send_json(404, {"error": "페이지를 찾을 수 없어요."})
        elif self.authorize_api():
            self.handle_auth(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"픽메뉴 http://127.0.0.1:{server.server_port}  |  종료 Ctrl+C", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
