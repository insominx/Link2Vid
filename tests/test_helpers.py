import unittest

from link2vid.core.helpers import get_format_options, normalize_url


class TestHelpers(unittest.TestCase):
    def test_normalize_url_enforces_https(self):
        self.assertEqual(
            normalize_url("http://example.com/path"),
            "https://example.com/path",
        )

    def test_normalize_url_rewrites_x(self):
        self.assertEqual(
            normalize_url("https://x.com/some/status"),
            "https://twitter.com/some/status",
        )

    def test_normalize_url_passthrough(self):
        self.assertEqual(normalize_url("example.com/path"), "example.com/path")

    def test_format_options_include_best_av(self):
        options = get_format_options()
        formats = {opt["format"] for opt in options}
        labels = {opt["label"] for opt in options}
        self.assertIn("bestvideo+bestaudio/best", formats)
        self.assertIn("Best (A+V)", labels)


if __name__ == "__main__":
    unittest.main()
