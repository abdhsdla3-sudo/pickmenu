"""로컬 픽메뉴에서 공유에 필요한 집계와 화면만 내보낸다."""

import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent / "pickmenu"
MENU_FIELDS = (
    "standard_menu_id", "name", "category", "components", "compound", "spice", "soup",
    "nutrition_status", "mapping_method",
)
REF_FIELDS = (
    "name", "food_id", "basis", "unit", "values", "original_basis", "serving_reference",
    "source", "source_url", "method", "confidence",
)


def reference(item: dict | None) -> dict | None:
    if item is None:
        return None
    return {key: item[key] for key in REF_FIELDS}


def shared_menu(item: dict) -> dict:
    return {
        **{key: item[key] for key in MENU_FIELDS},
        "regions": {region: {"reviews": value["reviews"], "stores": value["stores"]}
                    for region, value in item["regions"].items()},
        "nutrition": reference(item["nutrition"]),
        "references": [reference(ref) for ref in item["references"]],
    }


if __name__ == "__main__":
    configured = os.environ.get("PICKMENU_CATALOG_PATH")
    path = Path(configured).expanduser().resolve() if configured else LOCAL / "local" / "catalog.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        menus = [shared_menu(json.loads(row[0]))
                 for row in db.execute("SELECT data FROM catalog ORDER BY id")]
    if not menus:
        raise ValueError("공유할 메뉴가 없습니다.")
    (HERE / "catalog.json").write_text(
        json.dumps(menus, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    (HERE / "web").mkdir(exist_ok=True)
    photo_assets = sorted((LOCAL / "web").glob("menu-photos*.webp"))
    assets = ["index.html", "app.js", "style.css", "mark.svg", *(p.name for p in photo_assets)]
    for asset in assets:
        shutil.copyfile(LOCAL / "web" / asset, HERE / "web" / asset)
    print(f"공유 메뉴 {len(menus):,}개와 화면 파일 {len(assets)}개")
