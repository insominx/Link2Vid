from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from link2vid.core import CookiesRequiredError, NoTranscriptAvailableError
from link2vid.ui.main_window import BatchPromptPolicy, VideoDownloaderApp


class _UrlEntry:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class _BatchBar:
    def __init__(self) -> None:
        self.running = False
        self.status = ""
        self.summary = (0, 0)
        self.enabled = False

    def set_running(self, value): self.running = bool(value)
    def set_status(self, value): self.status = value
    def set_selection_summary(self, selected, total): self.summary = (selected, total)
    def set_enabled(self, value): self.enabled = bool(value)


def _bind(app, *names: str) -> None:
    for name in names:
        setattr(app, name, getattr(VideoDownloaderApp, name).__get__(app))


class BatchHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = object.__new__(VideoDownloaderApp)
        self.app.main_thread_id = threading.get_ident()
        self.app.video_entries = [
            {"title": "One", "url": "https://example.com/one"},
            {"title": "Two", "url": "https://example.com/two"},
            {"title": "Three", "url": "https://example.com/three"},
        ]
        self.app.entries_generation = 1
        self.app.selected_entry_indexes = {0, 1, 2}
        self.app.entry_format_by_index = {1: "bestaudio"}
        self.app.entry_transcript_by_index = {1: "fr"}
        self.app.card_index_by_card = {}
        self.app.card_by_index = {}
        self.app.card_entries = {}
        self.app.cards = []
        self.app.busy_cards = set()
        self.app.batch_run = None
        self.app.batch_prompt_policy = None
        self.app.batch_bar = _BatchBar()
        self.app.output_path = self.tmp.name
        self.app.is_fetching = False
        self.app.url_entry = _UrlEntry("https://example.com/playlist")
        self.app.format_options = [
            {"label": "Best (A+V)", "format": "bestvideo+bestaudio/best"},
            {"label": "Best audio", "format": "bestaudio"},
        ]
        self.app.format_label_map = {
            "bestvideo+bestaudio/best": "Best (A+V)",
            "bestaudio": "Best audio",
            "best": "Best (single file)",
        }
        self.app.ffmpeg_available = True
        self.app.log_history = []
        self.app.log = self.app.log_history.append
        self.app.log_error = lambda stage, exc: self.app.log_history.append(f"{stage}: {exc}")
        self.app.ui_warn = mock.Mock()
        self.app.ui_confirm = mock.Mock(return_value=True)
        self.app.ui_choose_cookies_browser = mock.Mock(return_value=None)
        self.app.cookies_browser = None
        self.app.ui_ffmpeg_fallback = mock.Mock(return_value="continue")
        self.app.set_progress = mock.Mock()
        self.app.invalidate_verified_media = mock.Mock(return_value=0)
        self.app.queue_card_busy = mock.Mock()
        self.app.queue_card_status = mock.Mock()
        self.app.queue_card_progress = mock.Mock()
        self.app.last_action_kind = None
        self.app.last_selected_format = None
        self.app.last_selected_label = None
        self.app.last_selected_title = None
        self.app.last_transcript_source = None
        self.app.last_transcript_languages = []
        _bind(
            self.app,
            "run_on_ui_thread", "entry_url_for_entry", "format_for_index",
            "transcript_track_for_index", "_selected_indexes_ordered",
            "_refresh_batch_bar", "_freeze_batch_items", "_begin_batch_item",
            "_record_batch_outcome", "_run_batch_worker", "_finish_batch",
            "choose_cookies_browser", "_prepare_cookie_retry",
            "confirm_format_fallback",
            "confirm_ffmpeg_fallback",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_multi_entry_url_resolution_never_falls_back_to_address_bar(self):
        self.assertEqual(
            self.app.entry_url_for_entry(self.app.video_entries[0]),
            "https://example.com/one",
        )
        self.assertIsNone(self.app.entry_url_for_entry({"title": "Thin"}))

    def test_batch_media_dispatches_in_index_order_with_frozen_formats(self):
        items, skipped, default_count = self.app._freeze_batch_items("media")
        self.assertEqual(skipped, 0)
        self.assertEqual(default_count, 2)
        run = self._make_run("media", items, skipped, default_count)
        calls = []
        self.app.download_video = lambda *args: calls.append(args) or True
        self.app._run_batch_worker(run)
        self.assertEqual([call[0] for call in calls], [
            "https://example.com/one", "https://example.com/two", "https://example.com/three"
        ])
        self.assertEqual([call[1] for call in calls], [
            "bestvideo+bestaudio/best", "bestaudio", "bestvideo+bestaudio/best"
        ])
        self.assertEqual((run.completed, run.failed, run.skipped), (3, 0, 0))
        self.assertFalse(self.app.batch_bar.running)
        self.assertIsNone(self.app.batch_prompt_policy)

    def test_false_and_exception_failures_do_not_stop_later_items(self):
        items, skipped, default_count = self.app._freeze_batch_items("media")
        run = self._make_run("media", items, skipped, default_count)
        outcomes = iter((False, RuntimeError("boom"), True))

        def runner(*_args):
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result

        self.app.download_video = runner
        self.app._run_batch_worker(run)
        self.assertEqual((run.completed, run.failed, run.skipped), (1, 2, 0))
        self.assertIn("Batch media finished: 1 complete, 2 failed, 0 skipped.", self.app.log_history)

    def test_transcript_batch_passes_per_entry_tracks_and_never_calls_media(self):
        items, skipped, default_count = self.app._freeze_batch_items("transcript")
        run = self._make_run("transcript", items, skipped, default_count)
        calls = []
        self.app.download_video = mock.Mock(side_effect=AssertionError("media called"))
        self.app.download_transcript = lambda *args: calls.append(args) or True
        self.app._run_batch_worker(run)
        self.assertEqual([call[3] for call in calls], [None, "fr", None])
        self.app.download_video.assert_not_called()
        self.assertEqual((run.completed, run.failed, run.skipped), (3, 0, 0))

    def test_freeze_precounts_unresolvable_and_direct_media_skips(self):
        self.app.video_entries[1] = {"title": "Thin"}
        self.app.video_entries[2]["_ffmpeg_headers"] = {"x": "y"}
        items, skipped, _default_count = self.app._freeze_batch_items("media")
        self.assertEqual([item.index for item in items], [0])
        self.assertEqual(skipped, 2)

    def test_cancel_skips_every_remaining_item(self):
        items, skipped, default_count = self.app._freeze_batch_items("media")
        run = self._make_run("media", items, skipped, default_count)
        calls = []

        def runner(*args):
            calls.append(args[0])
            run.cancel.set()
            return True

        self.app.download_video = runner
        self.app._run_batch_worker(run)
        self.assertEqual(calls, ["https://example.com/one"])
        self.assertEqual((run.completed, run.failed, run.skipped), (1, 0, 2))

    def test_session_replacement_skips_without_dispatching_new_urls(self):
        items, skipped, default_count = self.app._freeze_batch_items("media")
        run = self._make_run("media", items, skipped, default_count)
        self.app.entries_generation += 1
        self.app.video_entries = [{"title": "New", "url": "https://example.com/new"}]
        self.app.download_video = mock.Mock(return_value=True)
        self.app._run_batch_worker(run)
        self.app.download_video.assert_not_called()
        self.assertEqual((run.completed, run.failed, run.skipped), (0, 0, 3))
        self.assertTrue(any("session replaced" in line for line in self.app.log_history))

    def test_missing_ffmpeg_cancel_is_prompted_once_and_skips_merge_items(self):
        items, skipped, default_count = self.app._freeze_batch_items("media")
        run = self._make_run("media", items, skipped, default_count)
        self.app.ffmpeg_available = False
        self.app.ui_ffmpeg_fallback.return_value = "cancel"
        self.app.download_video = mock.Mock(return_value=True)
        self.app._run_batch_worker(run)
        self.assertEqual(self.app.ui_ffmpeg_fallback.call_count, 1)
        self.app.download_video.assert_called_once()
        self.assertEqual(self.app.download_video.call_args.args[1], "bestaudio")
        self.assertEqual((run.completed, run.failed, run.skipped), (1, 0, 2))

    def test_policy_caches_yes_and_no_once_per_kind(self):
        self.app.batch_prompt_policy = BatchPromptPolicy()
        self.app.ui_choose_cookies_browser.side_effect = ["brave", "chrome"]
        self.app.ui_confirm.side_effect = [True]
        self.assertEqual(self.app.choose_cookies_browser(), "brave")
        self.assertEqual(self.app.choose_cookies_browser(), "brave")
        self.assertTrue(self.app.confirm_format_fallback())
        self.assertTrue(self.app.confirm_format_fallback())
        self.assertEqual(self.app.ui_choose_cookies_browser.call_count, 1)
        self.assertEqual(self.app.ui_confirm.call_count, 1)

    def test_no_policy_prompts_every_time(self):
        self.app.batch_prompt_policy = None
        self.app.ui_choose_cookies_browser.return_value = "brave"
        self.app.choose_cookies_browser()
        self.app.choose_cookies_browser()
        self.assertEqual(self.app.ui_choose_cookies_browser.call_count, 2)

    def test_policy_is_safe_under_concurrent_calls(self):
        self.app.batch_prompt_policy = BatchPromptPolicy()

        def answer():
            time.sleep(0.01)
            return "brave"

        self.app.ui_choose_cookies_browser.side_effect = lambda: answer()
        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(self.app.choose_cookies_browser())
            )
            for _ in range(5)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, ["brave"] * 5)
        self.assertEqual(self.app.ui_choose_cookies_browser.call_count, 1)

    def _make_run(self, kind, items, skipped, default_count):
        from link2vid.ui.main_window import BatchRun
        run = BatchRun(kind, items, self.app.entries_generation, self.tmp.name)
        run.skipped = skipped
        run.default_format_count = default_count
        self.app.batch_run = run
        self.app.batch_prompt_policy = BatchPromptPolicy()
        self.app.batch_bar.set_running(True)
        return run


class RunnerReturnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = object.__new__(VideoDownloaderApp)
        self.app.main_thread_id = threading.get_ident()
        self.app.download_manager = mock.Mock()
        self.app.batch_prompt_policy = None
        self.app.ui_confirm = mock.Mock(return_value=False)
        self.app.ui_choose_cookies_browser = mock.Mock(return_value=None)
        self.app.cookies_path = None
        self.app.cookies_browser = None
        self.app.last_error_reason = None
        self.app.last_transcript_source = None
        self.app.last_transcript_languages = []
        self.app.format_label_map = {"best": "Best (single file)"}
        self.app.last_selected_format = None
        self.app.last_selected_label = None
        self.app.log = mock.Mock()
        self.app.log_error = mock.Mock()
        self.app.set_progress = mock.Mock()
        self.app.queue_card_progress = mock.Mock()
        self.app.queue_card_status = mock.Mock()
        self.app.queue_card_busy = mock.Mock()
        self.app._folder_snapshot = mock.Mock(return_value="snapshot")
        self.app._recent_download_outputs = mock.Mock(return_value=[])
        self.app._verify_download_outputs = mock.Mock(return_value=[])
        _bind(
            self.app, "download_video", "download_transcript",
            "choose_cookies_browser", "_prepare_cookie_retry",
            "confirm_format_fallback",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_media_download_failure_returns_false(self):
        self.app.download_manager.download.return_value = False
        self.assertFalse(self.app.download_video("https://x.test/v", "best", self.tmp.name))

    def test_media_verified_success_returns_true(self):
        output = Path(self.tmp.name) / "video.mp4"
        output.write_bytes(b"data")

        def download(_url, _format, _outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            post_hook(str(output))
            return True

        self.app.download_manager.download.side_effect = download
        self.app._verify_download_outputs.return_value = [SimpleNamespace(
            path=output, size=4, modified_at=0
        )]
        self.assertTrue(self.app.download_video("https://x.test/v", "best", self.tmp.name))

    def test_transcript_success_and_no_transcript_results(self):
        self.app.download_manager.download_transcript.return_value = SimpleNamespace(
            source="manual", languages=("en",)
        )
        self.assertTrue(self.app.download_transcript("https://x.test/v", self.tmp.name))
        self.app.download_manager.download_transcript.side_effect = NoTranscriptAvailableError("none")
        self.assertFalse(self.app.download_transcript("https://x.test/v", self.tmp.name))

    def test_declined_cookies_returns_false(self):
        self.app.download_manager.download.side_effect = CookiesRequiredError("cookies")
        self.assertFalse(self.app.download_video("https://x.test/v", "best", self.tmp.name))

    def test_audio_format_requests_mp3_extract(self):
        self.app.ffmpeg_available = True
        self.app.download_manager.download.return_value = False
        self.assertFalse(self.app.download_video("https://x.test/v", "bestaudio", self.tmp.name))
        _args, kwargs = self.app.download_manager.download.call_args
        self.assertTrue(kwargs["extract_audio"])

    def test_audio_format_skips_mp3_extract_without_ffmpeg(self):
        self.app.ffmpeg_available = False
        self.app.download_manager.download.return_value = False
        self.assertFalse(self.app.download_video("https://x.test/v", "bestaudio", self.tmp.name))
        _args, kwargs = self.app.download_manager.download.call_args
        self.assertFalse(kwargs["extract_audio"])


if __name__ == "__main__":
    unittest.main()
