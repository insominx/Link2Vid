import unittest

from link2vid.core.helpers import (
    get_format_options,
    normalize_url,
    url_from_clipboard_text,
)


class TestHelpers(unittest.TestCase):
    def test_normalize_url_enforces_https(self):
        self.assertEqual(
            normalize_url("http://example.com/path"),
            "https://example.com/path",
        )

    def test_normalize_url_preserves_host(self):
        self.assertEqual(
            normalize_url("https://example.com/some/status"),
            "https://example.com/some/status",
        )

    def test_normalize_url_passthrough(self):
        self.assertEqual(normalize_url("example.com/path"), "example.com/path")

    def test_url_from_clipboard_text_plain_url(self):
        self.assertEqual(
            url_from_clipboard_text("  https://video-host.example/watch?v=abcd  "),
            "https://video-host.example/watch?v=abcd",
        )

    def test_url_from_clipboard_text_embedded_url(self):
        self.assertEqual(
            url_from_clipboard_text("see https://example.com/foo?q=1 thanks"),
            "https://example.com/foo?q=1",
        )

    def test_url_from_clipboard_text_first_line_only(self):
        self.assertEqual(
            url_from_clipboard_text("https://a.com/\nhttps://b.com/"),
            "https://a.com/",
        )

    def test_url_from_clipboard_text_invalid_returns_none(self):
        self.assertIsNone(url_from_clipboard_text(""))
        self.assertIsNone(url_from_clipboard_text("not a url"))
        self.assertIsNone(url_from_clipboard_text("ftp://example.com/x"))

    def test_url_from_clipboard_text_trims_trailing_punct(self):
        self.assertEqual(
            url_from_clipboard_text("https://example.com/path)."),
            "https://example.com/path",
        )

    def test_format_options_include_best_av(self):
        options = get_format_options()
        formats = {opt["format"] for opt in options}
        labels = {opt["label"] for opt in options}
        self.assertIn("bestvideo+bestaudio/best", formats)
        self.assertIn("Best (A+V)", labels)


if __name__ == "__main__":
    unittest.main()
