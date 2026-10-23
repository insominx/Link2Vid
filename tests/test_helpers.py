import unittest

from doggybag.core.helpers import (
    AUDIO_EXTRACT_FORMAT,
    AUDIO_EXTRACT_FORMAT_SELECTOR,
    audio_extract_format_selector,
    audio_extract_postprocessors,
    entry_watch_url,
    get_format_options,
    is_audio_extract_format,
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

    def test_format_options_include_audio_mp3(self):
        options = get_format_options()
        audio = next(opt for opt in options if opt["format"] == AUDIO_EXTRACT_FORMAT)
        self.assertEqual(audio["label"], "Audio (MP3)")
        self.assertTrue(is_audio_extract_format(AUDIO_EXTRACT_FORMAT))
        self.assertFalse(is_audio_extract_format("bestvideo"))
        self.assertFalse(is_audio_extract_format(None))
        self.assertEqual(audio_extract_format_selector(), AUDIO_EXTRACT_FORMAT_SELECTOR)
        self.assertIn("best[acodec!=none]", AUDIO_EXTRACT_FORMAT_SELECTOR)
        processors = audio_extract_postprocessors()
        self.assertEqual(processors[0]["key"], "FFmpegExtractAudio")
        self.assertEqual(processors[0]["preferredcodec"], "mp3")

    def test_entry_watch_url_resolution_order(self):
        entry = {
            "webpage_url": "https://example.com/webpage",
            "url": "https://example.com/flat",
            "original_url": "https://example.com/original",
        }
        self.assertEqual(entry_watch_url(entry), "https://example.com/webpage")
        entry["webpage_url"] = "not-a-url"
        self.assertEqual(entry_watch_url(entry), "https://example.com/flat")
        entry["url"] = "opaque-id"
        self.assertEqual(entry_watch_url(entry), "https://example.com/original")

    def test_entry_watch_url_supports_flat_and_youtube_id_entries(self):
        self.assertEqual(
            entry_watch_url({"url": "https://www.youtube.com/watch?v=abcdefghijk"}),
            "https://www.youtube.com/watch?v=abcdefghijk",
        )
        self.assertEqual(
            entry_watch_url({"id": "abcdefghijk", "ie_key": "Youtube"}),
            "https://www.youtube.com/watch?v=abcdefghijk",
        )
        self.assertEqual(
            entry_watch_url({"id": "abc_def-123", "extractor_key": "YouTubeTab"}),
            "https://www.youtube.com/watch?v=abc_def-123",
        )

    def test_entry_watch_url_rejects_unusable_inputs(self):
        for value in (None, "video", [], {}, {"url": ""}):
            with self.subTest(value=value):
                self.assertIsNone(entry_watch_url(value))
        self.assertIsNone(entry_watch_url({"id": "abcdefghijk", "ie_key": "Vimeo"}))
        self.assertIsNone(entry_watch_url({"id": "too-short", "ie_key": "Youtube"}))


if __name__ == "__main__":
    unittest.main()
