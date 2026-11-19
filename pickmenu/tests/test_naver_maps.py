import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import naver_maps

GEOCODE = {"status": "OK", "addresses": [
    {"roadAddress": "경기도 수원시 팔달구 효원로 241", "x": "127.0286009", "y": "37.2635727"},
    {"jibunAddress": "경기도 수원시 팔달구 인계동 1111", "x": "127.03", "y": "37.26"},
    {"roadAddress": "좌표 없음"}]}
REVERSE = {"results": [{"name": "roadaddr", "region": {
    "area1": {"name": "경기도"}, "area2": {"name": "수원시 팔달구"}, "area3": {"name": "인계동"}},
    "land": {"name": "효원로", "number1": "241", "number2": ""}}]}


class NaverMapsTest(unittest.TestCase):
    def setUp(self):
        naver_maps._calls.clear()

    def test_credentials_from_labeled_file(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "naver.key"
            key.write_text("Client ID: abc\nClient Secret = xyz\n", encoding="utf-8")
            with patch.dict(os.environ, {"PICKMENU_NAVER_MAPS_KEY": str(key)}):
                self.assertEqual(naver_maps.credentials(), ("abc", "xyz"))
                self.assertTrue(naver_maps.available())
        with patch.dict(os.environ, {"PICKMENU_NAVER_MAPS_KEY": ""}):
            self.assertFalse(naver_maps.available())
            with self.assertRaises(naver_maps.Unavailable):
                naver_maps.geocode("효원로 241")

    def test_geocode_and_reverse_parse(self):
        with patch.object(naver_maps, "_call", return_value=GEOCODE):
            found = naver_maps.geocode("효원로 241")
        self.assertEqual(found, [{"address": "경기도 수원시 팔달구 효원로 241", "lat": 37.26357, "lng": 127.0286},
                                 {"address": "경기도 수원시 팔달구 인계동 1111", "lat": 37.26, "lng": 127.03}])
        with patch.object(naver_maps, "_call", return_value=REVERSE):
            self.assertEqual(naver_maps.reverse(37.26, 127.03), "경기도 수원시 팔달구 효원로 241")
        with patch.object(naver_maps, "_call", return_value={"results": []}):
            self.assertIsNone(naver_maps.reverse(37.26, 127.03))

    def test_rate_limit(self):
        with patch.object(naver_maps, "credentials", return_value=("a", "b")), \
                patch.object(naver_maps, "urlopen", side_effect=OSError):
            for _ in range(naver_maps.PER_MINUTE):
                with self.assertRaises(naver_maps.Unavailable):
                    naver_maps.geocode("x")
            with self.assertRaisesRegex(naver_maps.Unavailable, "많아요"):
                naver_maps.geocode("x")


if __name__ == "__main__":
    unittest.main()
