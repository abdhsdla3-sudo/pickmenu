import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402

WEB = Path(server.__file__).resolve().parent / "web"


class PhotoAssetTest(unittest.TestCase):
    def test_every_photo_name_has_a_file(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        block = re.search(r"const menuPhotoNames = \[(.*?)\];", app, re.S)
        assert block is not None
        names = json.loads("[" + block.group(1).rstrip().rstrip(",") + "]")
        self.assertEqual(len(names), len(set(names)))
        files = sorted(path.name for path in (WEB / "menu-photos").glob("*.webp"))
        self.assertEqual(files, [f"{i:03d}.webp" for i in range(1, len(names) + 1)])
        for name in files:
            self.assertIn(f"/menu-photos/{name}", server.ASSETS)
        self.assertLess(max((WEB / "menu-photos" / name).stat().st_size for name in files), 30_000)


class CatalogCacheTest(unittest.TestCase):
    def test_catalog_is_read_once_until_file_changes(self):
        menu = {"standard_menu_id": "MN_0000000000000001", "name": "김치찌개", "category": "찌개",
                "components": ["김치찌개"], "spice": "", "soup": ""}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalog.sqlite3"
            path.write_bytes(b"x")
            server._loaded.update(key=None, reference=None, menus=None)
            with (patch("server.catalog_path", return_value=path),
                  patch("server.catalog", return_value=[menu]) as read,
                  patch("menu_reco.reference_data", return_value=None)):
                first = server.prepared_menus()
                self.assertIs(server.prepared_menus(), first)
                self.assertEqual(read.call_count, 1)
                path.write_bytes(b"xy")
                import os
                os.utime(path, ns=(1, 1))
                server.prepared_menus()
                self.assertEqual(read.call_count, 2)
            server._loaded.update(key=None, reference=None, menus=None)


if __name__ == "__main__":
    unittest.main()
