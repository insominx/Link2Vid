import tempfile
import unittest
from pathlib import Path

from link2vid.core.cookie_prefs import (
    cookies_browser_label,
    load_preferred_cookies_browser,
    normalize_cookies_browser,
    store_preferred_cookies_browser,
)


class TestCookiePrefs(unittest.TestCase):
    def test_normalize_accepts_known_browsers(self):
        self.assertEqual(normalize_cookies_browser("Brave"), "brave")
        self.assertEqual(normalize_cookies_browser("CHROME"), "chrome")
        self.assertIsNone(normalize_cookies_browser("auto"))
        self.assertIsNone(normalize_cookies_browser(""))
        self.assertIsNone(normalize_cookies_browser("safari"))

    def test_label_for_auto_and_named_browser(self):
        self.assertEqual(cookies_browser_label(None), "Auto-detect")
        self.assertEqual(cookies_browser_label("brave"), "Brave")

    def test_user_settings_round_trip_and_dev_defaults_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertIsNone(load_preferred_cookies_browser(path=path))
            self.assertEqual(
                load_preferred_cookies_browser(
                    path=path,
                    dev_defaults={"cookies_browser": "edge"},
                ),
                "edge",
            )
            store_preferred_cookies_browser("brave", path=path)
            self.assertEqual(load_preferred_cookies_browser(path=path), "brave")
            store_preferred_cookies_browser(None, path=path)
            self.assertIsNone(load_preferred_cookies_browser(path=path))


if __name__ == "__main__":
    unittest.main()
