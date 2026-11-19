"""매장 주소를 네이버 클라우드 Maps Geocoding으로 좌표(경도·위도)로 바꾼다.

입력: 07_지도데이터의 `픽메뉴_635매장_네이버API_재점검_통합.xlsx` 시트 `01_매장635`
출력: 같은 폴더의 `매장좌표_네이버.csv`(UTF-8 BOM)와 실행 기록 `매장좌표_변환기록.md`

키는 화면에 출력하지 않는다. 다음 중 하나로 넘긴다.
  1) 환경변수 NCP_MAPS_KEY_ID, NCP_MAPS_KEY
  2) --key-file 로 지정한 메모장 파일(첫 줄 Client ID, 둘째 줄 Client Secret). 기본은 엑셀과 같은 폴더의 naver-maps.key

먼저 --limit 5로 시험하고, 문제가 없으면 --all로 전부 변환한다. 이미 변환한 매장은 건너뛰므로 중간에 멈춰도 다시 실행하면 이어서 한다.
수집 주소로 찾지 못하면 온라인 확인주소로 한 번 더 찾는다. 같은 주소는 한 번만 호출한다.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ENDPOINT = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode?query="
FIELDS = ["store_id", "store_name", "address_used", "address_kind", "road_address",
          "longitude", "latitude", "status", "checked_at"]
SHEET = "01_매장635"


class Stop(Exception):
    """키 오류·한도 초과처럼 더 호출해도 소용없는 오류."""


def keys(path: Path) -> tuple[str, str]:
    key_id, key = os.environ.get("NCP_MAPS_KEY_ID", ""), os.environ.get("NCP_MAPS_KEY", "")
    if key_id and key:
        return key_id, key
    if not path.exists():
        raise SystemExit(f"키를 찾을 수 없어요. 환경변수 NCP_MAPS_KEY_ID·NCP_MAPS_KEY를 정하거나 {path.name} 파일을 만들어 주세요.")
    # 값만 적어도 되고 Client ID: …, Client Secret: …처럼 이름을 붙여 적어도 된다.
    lines = [re.sub(r"^client\s*(id|secret)\s*[:=]\s*", "", line.strip(), flags=re.I)
             for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(lines) < 2:
        raise SystemExit(f"{path.name}에는 첫 줄 Client ID, 둘째 줄 Client Secret을 적어 주세요.")
    return lines[0], lines[1]


def clean(address: str | None) -> str:
    """'…로 10, 1층 (인계동)' → '…로 10'. 층·호수·괄호는 좌표 찾기에 방해가 된다."""
    text = " ".join(re.sub(r"\([^)]*\)", " ", address or "").split(",")[0].split())
    # 도로명 건물번호 뒤에 붙은 동·호수(세권로 185 1051 105호)는 뗀다.
    road = re.match(r"(.*?(?:로|길)\s*\d+(?:-\d+)?)(?:\s|$)", text)
    return road.group(1) if road else text


def geocode(address: str, key_id: str, key: str) -> dict[str, str]:
    request = Request(ENDPOINT + quote(address), headers={
        "x-ncp-apigw-api-key-id": key_id, "x-ncp-apigw-api-key": key, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
    except HTTPError as error:
        if error.code in (401, 403):
            raise Stop(f"인증 실패(HTTP {error.code}). 키와 콘솔의 Geocoding 이용 신청을 확인해 주세요.") from None
        if error.code == 429:
            raise Stop("호출 한도 초과(HTTP 429). 잠시 뒤 다시 실행해 주세요.") from None
        return {"status": f"HTTP {error.code}"}
    except (URLError, OSError, ValueError) as error:
        raise Stop(f"네이버 API에 연결할 수 없어요: {type(error).__name__}") from None
    found = data.get("addresses") or []
    if data.get("status") != "OK" or not found:
        return {"status": "찾지 못함"}
    first = found[0]
    return {"status": "OK" if len(found) == 1 else f"OK(후보 {len(found)}개 중 첫째)",
            "road_address": first.get("roadAddress") or first.get("jibunAddress") or "",
            "longitude": first.get("x", ""), "latitude": first.get("y", "")}


def stores(excel: Path) -> list[dict[str, str]]:
    from openpyxl import load_workbook

    book = load_workbook(excel, read_only=True, data_only=True)
    rows = book[SHEET].iter_rows(values_only=True)
    header = list(next(rows))
    at = {name: header.index(name) for name in ("매장ID", "매장명", "기존 수집주소", "온라인 확인주소")}
    return [{"store_id": str(row[at["매장ID"]]), "store_name": str(row[at["매장명"]] or ""),
             "collected": clean(row[at["기존 수집주소"]]), "online": clean(row[at["온라인 확인주소"]])}
            for row in rows if row[at["매장ID"]]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("excel", type=Path, help="픽메뉴_635매장_네이버API_재점검_통합.xlsx 경로")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--out", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--limit", type=int, help="이번에 새로 변환할 매장 수(시험용)")
    group.add_argument("--all", action="store_true", help="남은 매장을 모두 변환")
    args = parser.parse_args()
    folder = args.excel.resolve().parent
    out = args.out or folder / "매장좌표_네이버.csv"
    key_id, key = keys(args.key_file or folder / "naver-maps.key")

    done: dict[str, dict[str, str]] = {}
    if out.exists():
        with out.open(encoding="utf-8-sig", newline="") as handle:
            done = {row["store_id"]: row for row in csv.DictReader(handle)}
    targets = [s for s in stores(args.excel) if not done.get(s["store_id"], {}).get("status", "").startswith("OK")]
    if args.limit is not None:
        targets = targets[:args.limit]
    cache: dict[str, dict[str, str]] = {}
    calls = 0
    stopped = ""
    try:
        for number, store in enumerate(targets, 1):
            result: dict[str, str] = {"status": "주소 없음"}
            for kind, address in (("수집주소", store["collected"]), ("온라인 확인주소", store["online"])):
                if not address or (kind != "수집주소" and address == store["collected"]):
                    continue
                if address not in cache:
                    cache[address] = geocode(address, key_id, key)
                    calls += 1
                    time.sleep(0.1)
                result = {**cache[address], "address_used": address, "address_kind": kind}
                if result["status"].startswith("OK"):
                    break
            done[store["store_id"]] = {"store_id": store["store_id"], "store_name": store["store_name"],
                                       "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"), **result}
            if number % 50 == 0:
                print(f"{number}/{len(targets)}곳 처리", flush=True)
    except Stop as error:
        stopped = str(error)
        print(stopped, file=sys.stderr)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(done.values(), key=lambda row: row["store_id"]))
    ok = sum(1 for row in done.values() if row.get("status", "").startswith("OK"))
    several = sum(1 for row in done.values() if "후보" in row.get("status", ""))
    summary = (f"\n## {datetime.now():%Y-%m-%d %H:%M} 실행\n\n"
               f"- 방법: 네이버 클라우드 Maps Geocoding(`map-geocode/v2`). 수집 주소에서 층·괄호를 지우고 찾고, "
               f"못 찾으면 온라인 확인주소로 다시 찾는다. 원본 엑셀은 고치지 않았다.\n"
               f"- 이번 대상 {len(targets)}곳, API 호출 {calls}회\n"
               f"- 누적: 좌표 확보 {ok}곳 / 기록 {len(done)}곳 (후보가 여러 개라 첫째를 쓴 곳 {several}곳)\n"
               + (f"- 중단: {stopped}\n" if stopped else ""))
    log = folder / "매장좌표_변환기록.md"
    if not log.exists():
        log.write_text("# 매장 좌표 변환 기록\n\n결과 파일: `매장좌표_네이버.csv` (store_id = 통합 매장ID `SW_…`, "
                       "longitude = 경도, latitude = 위도)\n", encoding="utf-8")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
