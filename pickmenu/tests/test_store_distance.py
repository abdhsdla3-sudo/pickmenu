import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_menu_stores import build_catalog, menu

import menu_stores
import server

COORDS = """store_id,store_name,address_used,address_kind,road_address,longitude,latitude,status,checked_at
SW_a,A,,,,127.0300,37.2700,OK,
SW_b,B,,,,127.0010,37.2640,OK,
SW_c,C,,,,0,0,찾지 못함,
SW_x,X,,,,abc,37.0,OK,
"""


class StoreDistanceTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.coords = Path(self.folder.name) / "coords.csv"
        self.coords.write_text(COORDS, encoding="utf-8-sig")
        self.catalog = Path(self.folder.name) / "catalog.sqlite3"
        build_catalog(self.catalog)
        menu_stores._coords.update(key=None, value={})

    def tearDown(self):
        self.folder.cleanup()

    def test_distance_and_score(self):
        self.assertAlmostEqual(menu_stores.distance_km((37.27, 127.03), (37.27, 127.03)), 0)
        self.assertAlmostEqual(menu_stores.distance_km((37.0, 127.0), (38.0, 127.0)), 111.19, places=1)
        near, far = menu_stores.store_score(10, 0.5), menu_stores.store_score(10, 6.0)
        self.assertAlmostEqual(near - far, 40)
        self.assertAlmostEqual(menu_stores.store_score(10, None) - far, 20)  # 좌표 없음 = 중간

    def test_locations_skip_failed_and_bad_rows(self):
        with patch.dict(os.environ, {"PICKMENU_STORE_COORDS": str(self.coords)}):
            self.assertEqual(set(menu_stores.store_locations()), {"SW_a", "SW_b"})
        with patch.dict(os.environ, {"PICKMENU_STORE_COORDS": ""}):
            self.assertEqual(menu_stores.store_locations(), {})

    def test_location_reorders_stores_and_adds_distance(self):
        selected = [menu("최상급 육회")]  # SW_a 리뷰 5건, SW_b 리뷰 2건
        with patch.dict(os.environ, {"PICKMENU_STORE_COORDS": str(self.coords)}), \
                patch.object(menu_stores, "store_names", return_value={}):
            plain = menu_stores.stores(self.catalog, selected, "")
            self.assertEqual([s["reviews"] for s in plain["stores"]], [5, 2])
            self.assertNotIn("distance_km", plain["stores"][0])
            near_b = menu_stores.stores(self.catalog, selected, "", origin=(37.2640, 127.0010))
        self.assertTrue(near_b["location_used"])
        self.assertEqual(near_b["located"], 2)
        self.assertEqual([s["reviews"] for s in near_b["stores"]], [2, 5])  # 가까운 SW_b가 앞
        self.assertEqual(near_b["stores"][0]["distance_km"], 0.0)

    def test_location_payload_is_validated(self):
        self.assertIsNone(server.user_location({}))
        self.assertEqual(server.user_location({"location": {"lat": 37.26, "lng": 127.03}}), (37.26, 127.03))
        for bad in ("x", {"lat": True, "lng": 127}, {"lat": 10, "lng": 127}, {"lat": 37}, {"lat": "37", "lng": 127}):
            with self.assertRaises(ValueError):
                server.user_location({"location": bad})


if __name__ == "__main__":
    unittest.main()
