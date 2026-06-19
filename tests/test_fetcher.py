import unittest
from unittest.mock import patch

from link2vid.core.errors import CookiesRequiredError
from link2vid.core.fetcher import FetchError, FetchResults, NeedsCookies, NeedsSelenium, VideoFetcher
from tests.fixtures.hosts import AUTH_HOST_A, VIDEO_HOST_A


class TestFetcher(unittest.TestCase):
    def test_needs_cookies_for_auth_signals(self):
        fetcher = VideoFetcher(get_video_info=lambda *_: [])
        self.assertTrue(fetcher._needs_cookies(f"https://{VIDEO_HOST_A}/watch/abc", Exception("HTTP Error 403")))

    def test_needs_cookies_rejects_non_auth_signals(self):
        fetcher = VideoFetcher(get_video_info=lambda *_: [])
        self.assertFalse(fetcher._needs_cookies(f"https://{VIDEO_HOST_A}/video", Exception("timeout")))

    def test_fetch_returns_needs_cookies_for_cookie_error(self):
        def raise_cookie(*_args, **_kwargs):
            raise CookiesRequiredError()

        fetcher = VideoFetcher(get_video_info=raise_cookie)
        outcome = fetcher.fetch(f"https://{VIDEO_HOST_A}/watch/abc")
        self.assertIsInstance(outcome, NeedsCookies)

    def test_fetch_returns_needs_cookies_for_cookie_signal(self):
        def raise_err(*_args, **_kwargs):
            raise Exception("HTTP Error 403: Forbidden")

        fetcher = VideoFetcher(get_video_info=raise_err)
        with patch("link2vid.core.fetcher.extract_embedded_page_videos", return_value=[]), patch(
            "link2vid.core.fetcher.scan_direct_media_entries", return_value=[]
        ), patch(
            "link2vid.core.fetcher.scan_direct_m3u8", return_value=None
        ):
            outcome = fetcher.fetch(f"https://{VIDEO_HOST_A}/watch/abc")
        self.assertIsInstance(outcome, NeedsCookies)

    def test_fetch_returns_needs_selenium_for_other_errors(self):
        def raise_err(*_args, **_kwargs):
            raise Exception("some other failure")

        fetcher = VideoFetcher(get_video_info=raise_err)
        with patch("link2vid.core.fetcher.extract_embedded_page_videos", return_value=[]), patch(
            "link2vid.core.fetcher.scan_direct_media_entries", return_value=[]
        ), patch(
            "link2vid.core.fetcher.scan_direct_m3u8", return_value=None
        ):
            outcome = fetcher.fetch(f"https://{VIDEO_HOST_A}/video")
        self.assertIsInstance(outcome, NeedsSelenium)

    def test_fetch_returns_error_for_terminal_no_video_error(self):
        def raise_err(*_args, **_kwargs):
            raise Exception("No video could be found on this page")

        fetcher = VideoFetcher(get_video_info=raise_err)
        outcome = fetcher.fetch(f"https://{VIDEO_HOST_A}/status/1")
        self.assertIsInstance(outcome, FetchError)

    def test_fetch_returns_results_on_success(self):
        entries = [{"title": "ok"}]
        fetcher = VideoFetcher(get_video_info=lambda *_: entries)
        outcome = fetcher.fetch(f"https://{VIDEO_HOST_A}/video")
        self.assertIsInstance(outcome, FetchResults)
        self.assertEqual(outcome.entries, entries)

    def test_embedded_page_extractor_is_gated_by_dev_domain(self):
        def raise_err(*_args, **_kwargs):
            raise Exception("some other failure")

        fetcher = VideoFetcher(get_video_info=raise_err, dev_defaults={"sites": [{"domain": AUTH_HOST_A}]})
        with patch("link2vid.core.fetcher.extract_embedded_page_videos", return_value=[{"title": "ok"}]) as extractor:
            outcome = fetcher.fetch(f"https://www.{AUTH_HOST_A}/watch/abc")
        self.assertIsInstance(outcome, FetchResults)
        extractor.assert_called_once()


if __name__ == "__main__":
    unittest.main()
