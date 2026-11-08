"""픽메뉴 로컬 HTTP 서버."""

import argparse
import hmac
import json
import os
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from raw import BASE, LOCAL
from recommend import Profile, recommend

CATALOG = LOCAL / "catalog.sqlite3"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/mark.svg": ("mark.svg", "image/svg+xml"),
}


def catalog(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        configured = os.environ.get("PICKMENU_CATALOG_PATH")
        path = Path(configured).expanduser().resolve() if configured else CATALOG
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return [json.loads(value) for value, in db.execute("SELECT data FROM catalog ORDER BY id")]


def categories(menus: list[dict[str, Any]]) -> list[str]:
    return sorted({menu["category"] for menu in menus})


def public_menu(menu: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in menu.items() if key not in {"regions", "mapping_note"}}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, kind: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, value: Any) -> None:
        self.reply(status, "application/json; charset=utf-8",
                   json.dumps(value, ensure_ascii=False).encode("utf-8"))

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
            menus = catalog()
            if not menus:
                raise ValueError("빈 카탈로그")
            return menus
        except (OSError, sqlite3.Error, ValueError) as error:
            self.log_error("카탈로그 오류 %s", error)
            self.send_json(503, {"error": "추천 데이터를 열 수 없어요. 전처리를 완료한 뒤 다시 시도해 주세요."})
            return None

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/") and not self.authorize_api():
            return
        if path in ASSETS:
            filename, content_type = ASSETS[path]
            self.reply(200, content_type, (BASE / "web" / filename).read_bytes())
        elif path == "/api/catalog":
            if (menus := self.load()) is not None:
                self.send_json(200, {"categories": categories(menus), "menu_count": len(menus),
                                     "period": "2026년 7~9월"})
        elif path.startswith("/api/menus/"):
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
        if self.path != "/api/recommend":
            self.send_json(404, {"error": "페이지를 찾을 수 없어요."})
            return
        if not self.authorize_api():
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "입력 형식을 확인해 주세요."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
        except ValueError:
            self.send_json(400, {"error": "입력 형식 또는 길이를 확인해 주세요."})
            return
        if (menus := self.load()) is None:
            return
        try:
            profile = Profile.parse(payload, categories(menus))
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return
        result = recommend(menus, profile)
        result["items"] = [public_menu(menu) for menu in result["items"]]
        self.send_json(200, result)


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
