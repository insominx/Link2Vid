import unittest
from unittest.mock import patch

from link2vid.core.errors import CookiesRequiredError
from link2vid.core.fetcher import FetchResults, NeedsCookies, NeedsSelenium, VideoFetcher


class TestFetcher(unittest.TestCase):
    def test_needs_cookies_for_youtube_signals(self):
        fetcher = VideoFetcher(get_video_info=lambda *_: [])
        self.assertTrue(fetcher._needs_cookies("https://youtube.com/watch?v=abc", Exception("HTTP Error 403")))

    def test_needs_cookies_rejects_other_domains(self):
        fetcher = VideoFetcher(get_video_info=lambda *_: [])
        self.assertFalse(fetcher._needs_cookies("https://example.com/video", Exception("HTTP Error 403")))

    def test_fetch_returns_needs_cookies_for_cookie_error(self):
        def raise_cookie(*_args, **_kwargs):
            raise CookiesRequiredError()

        fetcher = VideoFetcher(get_video_info=raise_cookie)
        outcome = fetcher.fetch("https://youtube.com/watch?v=abc")
        self.assertIsInstance(outcome, NeedsCookies)

    def test_fetch_returns_needs_cookies_for_cookie_signal(self):
        def raise_err(*_args, **_kwargs):
            raise Exception("HTTP Error 403: Forbidden")

        fetcher = VideoFetcher(get_video_info=raise_err)
        with patch("link2vid.core.fetcher.extract_linkedin_videos", return_value=[]), patch(
            "link2vid.core.fetcher.scan_direct_m3u8", return_value=None
        ):
            outcome = fetcher.fetch("https://youtube.com/watch?v=abc")
        self.assertIsInstance(outcome, NeedsCookies)

    def test_fetch_returns_needs_selenium_for_other_errors(self):
        def raise_err(*_args, **_kwargs):
            raise Exception("some other failure")

        fetcher = VideoFetcher(get_video_info=raise_err)
        with patch("link2vid.core.fetcher.extract_linkedin_videos", return_value=[]), patch(
            "link2vid.core.fetcher.scan_direct_m3u8", return_value=None
        ):
            outcome = fetcher.fetch("https://example.com/video")
        self.assertIsInstance(outcome, NeedsSelenium)

    def test_fetch_returns_results_on_success(self):
        entries = [{"title": "ok"}]
        fetcher = VideoFetcher(get_video_info=lambda *_: entries)
        outcome = fetcher.fetch("https://example.com/video")
        self.assertIsInstance(outcome, FetchResults)
        self.assertEqual(outcome.entries, entries)


if __name__ == "__main__":
    unittest.main()
