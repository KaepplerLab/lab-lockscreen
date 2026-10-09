"""Failure handling and NOAA format compatibility; no external requests."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import update
import migrate_config
from PIL import Image


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.config = update.load_config(Path(__file__).resolve().parents[1] / "config.toml")
        self.now = time.time()
        output = io.BytesIO()
        Image.new("RGB", (300, 300), "red").save(output, format="PNG")
        self.image = output.getvalue()

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, name, age=0):
        entry = {"data": base64.b64encode(self.image).decode(),
                 "hash": hashlib.sha256(self.image).hexdigest(),
                 "changed_at": self.now - age, "observed_at": self.now - age,
                 "checked_at": self.now - age, "url": "https://example.org/image.png"}
        path = self.state / (name + ".json")
        update.atomic_write(path, json.dumps(entry).encode())
        return path.read_bytes()

    def test_failed_download_preserves_last_good_source(self):
        before = self.seed("ovation")
        fetcher = update.Fetcher(self.config["update"])
        with patch.object(fetcher, "get", side_effect=OSError("offline")):
            result = update.update_source("ovation", self.config["sources"]["ovation"], fetcher, self.state, self.now, False)
        self.assertEqual((self.state / "ovation.json").read_bytes(), before)
        self.assertEqual(result["state"], "CACHED")
        self.assertIsNotNone(result["image"])

    def test_http_200_html_does_not_replace_good_image(self):
        before = self.seed("ovation")
        fetcher = update.Fetcher(self.config["update"])
        with patch.object(fetcher, "get", return_value=(b"<html>upstream error</html>", None)):
            result = update.update_source("ovation", self.config["sources"]["ovation"], fetcher, self.state, self.now, False)
        self.assertEqual((self.state / "ovation.json").read_bytes(), before)
        self.assertEqual(result["state"], "CACHED")

    def test_frozen_image_is_stale_despite_successful_fetch(self):
        self.seed("ovation", age=7200)
        fetcher = update.Fetcher(self.config["update"])
        with patch.object(fetcher, "get", return_value=(self.image, self.now)):
            result = update.update_source("ovation", self.config["sources"]["ovation"], fetcher, self.state, self.now, False)
        self.assertEqual(result["state"], "STALE")

    def test_offline_first_run_still_produces_valid_screen(self):
        fetcher = update.Fetcher(self.config["update"])
        result = update.update_source("ovation", self.config["sources"]["ovation"], fetcher, self.state, self.now, True)
        self.assertEqual(result["state"], "UNAVAILABLE")
        screen = update.compose(self.config, {"ovation": result}, self.now)
        update.publish(screen, self.state, False)
        image = update.decode_image((self.state / "current.png").read_bytes())
        self.assertEqual(image.size, (1920, 1080))

    def test_stale_cache_is_labelled(self):
        self.seed("ovation", age=7200)
        result = update.update_source("ovation", self.config["sources"]["ovation"], None, self.state, self.now, True)
        self.assertEqual(result["state"], "STALE")

    def test_corrupt_cache_does_not_crash(self):
        (self.state / "ovation.json").write_text('{"data":"wrong"}')
        result = update.update_source("ovation", self.config["sources"]["ovation"], None, self.state, self.now, True)
        self.assertEqual(result["state"], "UNAVAILABLE")

    def test_missing_cache_timestamps_do_not_crash(self):
        self.seed("ovation")
        path = self.state / "ovation.json"
        data = json.loads(path.read_text())
        del data["changed_at"]
        path.write_text(json.dumps(data))
        result = update.update_source("ovation", self.config["sources"]["ovation"], None, self.state, self.now, True)
        self.assertEqual(result["state"], "UNAVAILABLE")

    def test_generations_remain_bounded(self):
        image = Image.new("RGB", (300, 300), "blue")
        for _ in range(4):
            update.publish(image, self.state, False)
        self.assertEqual(len(list(self.state.glob("dashboard-*.png"))), 2)

    def test_noaa_old_and_new_kp_formats(self):
        date = "2026-01-01 00:00:00"
        old = [["time_tag", "Kp"], [date, "4.67"]]
        new = [{"time_tag": date, "Kp": 4.67}]
        self.assertEqual(update.metric(json.dumps(old).encode(), "kp"), update.metric(json.dumps(new).encode(), "kp"))

    def test_current_noaa_summary_products(self):
        wind = [{"proton_speed": 374, "time_tag": "2026-10-09T15:40:00Z"}]
        mag = [{"bt": 7, "bz_gsm": 5, "time_tag": "2026-10-09T15:16:00Z"}]
        self.assertEqual(update.metric(json.dumps(wind).encode(), "speed")[0], 374)
        self.assertEqual(update.metric(json.dumps(mag).encode(), "bz")[0], 5)

    def test_upgrade_keeps_identity_and_custom_source(self):
        config = self.state / "config.toml"
        old = '''[identity]\nlaboratory_name = "My Lab"\n[sources.speed]\nurl = "https://services.swpc.noaa.gov/products/solar-wind/plasma-2-hour.json"\n[sources.uaf]\nurl = "https://example.org/custom.png"\n'''
        config.write_text(old)
        migrate_config.migrate(config)
        revised = update.tomllib.loads(config.read_text())
        self.assertEqual(revised["identity"]["laboratory_name"], "My Lab")
        self.assertEqual(revised["sources"]["uaf"]["url"], "https://example.org/custom.png")
        self.assertIn("summary/solar-wind-speed.json", revised["sources"]["speed"]["url"])
        self.assertEqual(config.with_name("config.toml.before-v1.0.1").read_text(), old)

    def test_uaf_absolute_http_image_is_upgraded_to_https(self):
        config = self.config["sources"]["uaf"]
        fetcher = update.Fetcher(self.config["update"])
        page = b'<html><img src="http://optics.gi.alaska.edu/current/pkr-rgb.png"></html>'
        urls = []
        def get(url, **kwargs):
            urls.append(url)
            return (page, None) if len(urls) == 1 else (self.image, None)
        with patch.object(fetcher, "get", side_effect=get):
            fetcher.uaf(config)
        self.assertEqual(urls[-1], "https://optics.gi.alaska.edu/current/pkr-rgb.png")

    def test_grid_resolves_four_images_in_page_order(self):
        html = b'<img src="logo.png"><img src="one.png" alt="One"><img src="/two.jpg"><img src="http://optics.gi.alaska.edu/three.png"><img src="four.png?latest=1">'
        images = update.grid_images(html, "https://optics.gi.alaska.edu/asc_grid.html")
        self.assertEqual(len(images), 4)
        self.assertEqual(images[0]["label"], "One")
        self.assertEqual(images[1]["url"], "https://optics.gi.alaska.edu/two.jpg")
        self.assertEqual(images[2]["url"], "https://optics.gi.alaska.edu/three.png")
        self.assertTrue(images[3]["url"].endswith("four.png?latest=1"))

    def test_grid_does_not_guess_when_page_has_wrong_image_count(self):
        with self.assertRaises(ValueError):
            update.grid_images(b'<img src="one.png">', "https://optics.gi.alaska.edu/asc_grid.html")

    def test_one_failed_camera_keeps_its_cache_and_updates_others(self):
        cfg = dict(self.config["sources"]["uaf"])
        cfg["image_urls"] = [f"https://example.org/camera{i}.png" for i in range(4)]
        self.seed("uaf_2")
        before = (self.state / "uaf_2.json").read_bytes()
        fetcher = update.Fetcher(self.config["update"])
        def get(url, **kwargs):
            if "camera1.png" in url:
                raise OSError("Camera unavailable")
            return self.image, self.now
        with patch.object(fetcher, "get", side_effect=get):
            sources = update.update_grid(cfg, fetcher, self.state, self.now, False)
        self.assertEqual(sources["uaf_2"]["state"], "CACHED")
        self.assertEqual((self.state / "uaf_2.json").read_bytes(), before)
        self.assertEqual(sources["uaf_4"]["state"], "AVAILABLE")
        image = update.compose(self.config, sources, self.now)
        self.assertEqual(image.size, (1920, 1080))

    def test_grid_upgrade_retains_identity_and_is_idempotent(self):
        path = self.state / "config.toml"
        path.write_text('[identity]\nlaboratory_name="Test Lab"\n[sources.uaf]\nurl="https://example.org/old.png"\npage_url="https://optics.gi.alaska.edu/optics/realtime/"\n[sources.kp]\nenabled=true\n')
        migrate_config.migrate_grid(path)
        revised = path.read_text()
        parsed = update.tomllib.loads(revised)
        self.assertEqual(parsed["identity"]["laboratory_name"], "Test Lab")
        self.assertEqual(parsed["sources"]["uaf"]["mode"], "grid")
        self.assertEqual(parsed["sources"]["uaf"]["page_url"], "https://optics.gi.alaska.edu/asc_grid.html")
        migrate_config.migrate_grid(path)
        self.assertEqual(path.read_text(), revised)

    def test_null_latest_measurement_falls_back_to_valid_row(self):
        data = [{"time_tag": "2026-01-01 00:00:00", "bz_gsm": -3},
                {"time_tag": "2026-01-01 00:01:00", "bz_gsm": None}]
        self.assertEqual(update.metric(json.dumps(data).encode(), "bz")[0], -3)

    def test_oversized_download_is_rejected(self):
        fetcher = update.Fetcher({"timeout_seconds": 1, "attempts": 1, "max_download_mb": 1})
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"x" * (1024 * 1024 + 1)
        with patch.object(update, "urlopen", return_value=response):
            with self.assertRaises(ValueError):
                fetcher.get("https://example.org/test")


if __name__ == "__main__":
    unittest.main()
