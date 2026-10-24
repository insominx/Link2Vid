"""Stall fallback, raise_errors and flat listing in DownloadManager."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from doggybag.core.downloader import DownloadManager, _StallWatch
from doggybag.core.error_classification import classify_error
from doggybag.core.errors import DownloadStalledError
from tests.fixtures.hosts import VIDEO_HOST_A

YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"
MIB = 1024 * 1024


class DummyLogger:
    def debug(self, _msg):
        return None

    def warning(self, _msg):
        return None

    def error(self, _msg):
        return None


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def crawl(clock: FakeClock, hooks, *, seconds: int, rate: float, total: float, start: float = 0.0) -> None:
    """Feed progress events every 5 s at ``rate`` bytes/s."""
    downloaded = start
    for _ in range(seconds // 5 + 1):
        event = {
            "status": "downloading",
            "downloaded_bytes": downloaded,
            "total_bytes": total,
            "tmpfilename": "video.part",
        }
        for hook in hooks:
            hook(event)
        clock.now += 5
        downloaded += rate * 5


def fake_ydl(seen: list, script):
    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, _urls):
            youtube_args = (self.opts.get("extractor_args") or {}).get("youtube") or {}
            client = (youtube_args.get("player_client") or [None])[0]
            hooks = list(self.opts.get("progress_hooks") or [])
            seen.append((client, any(isinstance(hook, _StallWatch) for hook in hooks)))
            script(client, hooks)

    return FakeYoutubeDL


class TestStallFallback(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.manager = DownloadManager(ydl_logger=DummyLogger(), log_error=Mock())
        self.manager._clock = self.clock
        self.seen: list = []

    def _download(self, script, url: str = YOUTUBE_URL, **kwargs) -> bool:
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", fake_ydl(self.seen, script)):
            return self.manager.download(url, "best", "out", **kwargs)

    def test_crawling_youtube_download_falls_back_to_player_client(self) -> None:
        def script(client, hooks):
            if client is None:
                crawl(self.clock, hooks, seconds=120, rate=10 * 1024, total=500 * MIB)

        self.assertTrue(self._download(script))
        self.assertEqual([client for client, _ in self.seen], [None, "web_embedded"])
        self.assertEqual(self.manager.last_player_client, "web_embedded")

    def test_timeout_youtube_download_falls_back_to_player_client(self) -> None:
        def script(client, hooks):
            if client is None:
                raise Exception("Read timed out.")

        self.assertTrue(self._download(script))
        self.assertEqual([client for client, _ in self.seen], [None, "web_embedded"])

    def test_nearly_finished_slow_download_is_not_aborted(self) -> None:
        def script(client, hooks):
            crawl(self.clock, hooks, seconds=60, rate=10 * 1024, total=MIB, start=0)

        self.assertTrue(self._download(script))
        self.assertEqual([client for client, _ in self.seen], [None])

    def test_stall_watch_waits_for_grace_period(self) -> None:
        watch = _StallWatch(clock=self.clock)
        crawl(self.clock, [watch], seconds=40, rate=1024, total=500 * MIB)
        with self.assertRaises(DownloadStalledError):
            crawl(self.clock, [watch], seconds=10, rate=1024, total=500 * MIB, start=41 * 1024)

    def test_stall_detection_is_youtube_only(self) -> None:
        def script(client, hooks):
            crawl(self.clock, hooks, seconds=120, rate=1024, total=500 * MIB)

        self.assertTrue(self._download(script, url=f"https://{VIDEO_HOST_A}/watch/1"))
        self.assertEqual(self.seen, [(None, False)])

    def test_all_clients_stalling_gets_one_unguarded_attempt(self) -> None:
        def script(client, hooks):
            crawl(self.clock, hooks, seconds=120, rate=1024, total=500 * MIB)

        self.assertTrue(self._download(script))
        self.assertEqual(
            self.seen,
            [
                (None, True),
                ("web_embedded", True),
                ("tv_embedded", True),
                ("mweb", True),
                (None, False),
            ],
        )

    def test_unguarded_failure_raises_with_raise_errors(self) -> None:
        def script(client, hooks):
            if any(isinstance(hook, _StallWatch) for hook in hooks):
                crawl(self.clock, hooks, seconds=120, rate=1024, total=500 * MIB)
            raise Exception("connection reset")

        with self.assertRaises(Exception) as ctx:
            self._download(script, raise_errors=True)
        self.assertIn("connection reset", str(ctx.exception))
        self.manager.log_error.assert_not_called()

    def test_user_progress_hook_still_receives_events(self) -> None:
        events = []

        def script(client, hooks):
            crawl(self.clock, hooks, seconds=10, rate=10 * MIB, total=500 * MIB)

        self.assertTrue(self._download(script, progress_hook=events.append))
        self.assertTrue(events)


class TestDownloadContract(unittest.TestCase):
    def test_download_raise_errors_reraises_final_failure_without_logging(self) -> None:
        log_error = Mock()
        manager = DownloadManager(ydl_logger=DummyLogger(), log_error=log_error)

        def script(client, hooks):
            raise Exception("ERROR: Requested format is not available")

        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", fake_ydl([], script)):
            with self.assertRaises(Exception):
                manager.download(f"https://{VIDEO_HOST_A}/watch/1", "137", "out", raise_errors=True)
            self.assertFalse(manager.download(f"https://{VIDEO_HOST_A}/watch/1", "137", "out"))
        log_error.assert_called_once()

    def test_download_resets_last_player_client(self) -> None:
        manager = DownloadManager(ydl_logger=DummyLogger())
        manager.last_player_client = "web_embedded"
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", fake_ydl([], lambda *_: None)):
            self.assertTrue(manager.download(YOUTUBE_URL, "best", "out"))
        self.assertIsNone(manager.last_player_client)

    def test_get_video_info_flat_sets_extract_flat(self) -> None:
        captured = []

        class FakeYoutubeDL:
            def __init__(self, opts):
                captured.append(opts)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, _url, download=False):
                return {"entries": [{"id": "a"}, {"id": "b"}]}

        manager = DownloadManager(ydl_logger=DummyLogger())
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            entries = manager.get_video_info(f"https://{VIDEO_HOST_A}/list", flat=True)
            manager.get_video_info(f"https://{VIDEO_HOST_A}/list")
        self.assertEqual(len(entries), 2)
        self.assertEqual(captured[0].get("extract_flat"), "in_playlist")
        self.assertNotIn("extract_flat", captured[1])

    def test_stall_message_classifies_as_media_stall(self) -> None:
        reason, hint = classify_error(str(DownloadStalledError(rate=2048, window=30)))
        self.assertEqual(reason, "media stall/player client")
        self.assertTrue(hint)


if __name__ == "__main__":
    unittest.main()
