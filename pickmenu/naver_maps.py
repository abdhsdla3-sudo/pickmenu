"""배달 받을 위치를 주소로 찾기(Geocoding)와 지도에서 찍은 위치의 주소 보기(Reverse Geocoding).

네이버 클라우드 Maps 키는 PICKMENU_NAVER_MAPS_KEY에 지정한 파일(첫 줄 Client ID, 둘째 줄 Client Secret,
`Client ID: …` 형식도 가능)에서 읽는다. 키가 없으면 주소 기능만 꺼지고 지도·거리 추천은 그대로 동작한다.
사용자가 입력한 주소와 찍은 좌표는 이 호출에만 쓰고 저장하거나 로그에 남기지 않는다.
"""

import json
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

GEOCODE = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode?query="
REVERSE = "https://maps.apigw.ntruss.com/map-reversegeocode/v2/gc?output=json&orders=roadaddr,addr&coords="
# 서버 전체에서 1분에 이만큼만 호출한다(무료 이용량 보호, 남용 방지).
PER_MINUTE = 60
_calls: deque[float] = deque()
_lock = threading.Lock()


class Unavailable(Exception):
    """키가 없거나, 한도를 넘었거나, 네이버 API를 쓸 수 없을 때."""


def credentials() -> tuple[str, str] | None:
    configured = os.environ.get("PICKMENU_NAVER_MAPS_KEY")
    if not configured:
        return None
    try:
        lines = [re.sub(r"^client\s*(id|secret)\s*[:=]\s*", "", line.strip(), flags=re.I)
                 for line in Path(configured).expanduser().read_text(encoding="utf-8-sig").splitlines()
                 if line.strip()]
    except OSError:
        return None
    return (lines[0], lines[1]) if len(lines) >= 2 else None


def available() -> bool:
    return credentials() is not None


def _call(url: str) -> dict[str, Any]:
    keys = credentials()
    if keys is None:
        raise Unavailable("주소 검색을 아직 쓸 수 없어요. 지도에서 직접 찍어 주세요.")
    with _lock:
        now = time.monotonic()
        while _calls and now - _calls[0] > 60:
            _calls.popleft()
        if len(_calls) >= PER_MINUTE:
            raise Unavailable("주소 검색이 많아요. 잠시 뒤 다시 시도해 주세요.")
        _calls.append(now)
    request = Request(url, headers={"x-ncp-apigw-api-key-id": keys[0], "x-ncp-apigw-api-key": keys[1],
                                    "Accept": "application/json"})
    try:
        with urlopen(request, timeout=6) as response:
            raw = response.read(200_001)
        if len(raw) > 200_000:
            raise ValueError("large response")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (HTTPError, URLError, OSError, ValueError) as error:
        raise Unavailable("주소를 찾지 못했어요. 잠시 뒤 다시 시도하거나 지도에서 찍어 주세요.") from error


def geocode(query: str) -> list[dict[str, Any]]:
    """주소 → 후보 최대 5개 [{address, lat, lng}]."""
    data = _call(GEOCODE + quote(query))
    found = []
    for item in (data.get("addresses") or [])[:5]:
        try:
            lat, lng = float(item["y"]), float(item["x"])
        except (KeyError, TypeError, ValueError):
            continue
        found.append({"address": item.get("roadAddress") or item.get("jibunAddress") or query,
                      "lat": round(lat, 5), "lng": round(lng, 5)})
    return found


def reverse(lat: float, lng: float) -> str | None:
    """좌표 → 도로명 주소(없으면 지번). 찾지 못하면 None."""
    data = _call(REVERSE + quote(f"{lng},{lat}"))
    for result in data.get("results") or []:
        region = result.get("region") or {}
        parts = [(region.get(f"area{i}") or {}).get("name", "") for i in range(1, 5)]
        land = result.get("land") or {}
        if result.get("name") == "roadaddr":
            parts = parts[:2] + [land.get("name", "")]
        number = land.get("number1", "")
        if number and land.get("number2"):
            number += f"-{land['number2']}"
        text = " ".join(part for part in [*parts, number] if part)
        if text:
            return text
    return None
