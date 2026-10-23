"""Controller ↔ frontend seam: presenter batching, dialogs, and page intents."""

from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from doggybag.app.controller import AppController, entry_details
from doggybag.ui.webview_app import Bridge, WebViewPresenter


class _RecordingView:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []
        self.answer = None
        self.notices: list[tuple[str, str]] = []

    def emit(self, event, payload):
        self.events.append((event, payload))

    def request(self, kind, payload):
        self.requests.append((kind, payload))
        future: Future = Future()
        future.set_result(self.answer)
        return future

    def notify(self, level, title, message=""):
        self.notices.append((level, title))

    def set_clipboard(self, text):
        return True

    def get_clipboard(self):
        return None

    def pick_folder(self, initial=None):
        return None

    def pick_file(self, title, initial=None):
        return None

    def named(self, event):
        return [payload for name, payload in self.events if name == event]


def _controller(view) -> AppController:
    tmp = tempfile.mkdtemp()
    with ExitStack() as stack:
        stack.enter_context(mock.patch("doggybag.app.controller.DownloadManager"))
        stack.enter_context(mock.patch("doggybag.app.controller.VideoFetcher"))
        stack.enter_context(mock.patch("doggybag.app.controller.get_yt_dlp_version", return_value="test"))
        stack.enter_context(mock.patch("doggybag.core.runtime.startup_summary", return_value="runtime"))
        stack.enter_context(mock.patch.object(AppController, "load_dev_defaults", return_value={}))
        stack.enter_context(mock.patch.object(AppController, "get_default_output_path", return_value=tmp))
        stack.enter_context(
            mock.patch.object(AppController, "_default_debug_log_path", return_value=str(Path(tmp) / "debug.log"))
        )
        stack.enter_context(mock.patch.object(AppController, "check_ffmpeg"))
        stack.enter_context(mock.patch.object(AppController, "check_js_runtime"))
        service = mock.Mock()
        service.list_available_models.return_value = ["small"]
        app = AppController(view, whisper_service=service, audio_source_resolver=mock.Mock())
    app.download_manager.transcript_tracks_from_info.return_value = []
    app.ffmpeg_available = True
    return app


class TestPresenterBatching(unittest.TestCase):
    def setUp(self) -> None:
        self.presenter = WebViewPresenter()
        self.addCleanup(self.presenter.close)

    def pending(self):
        with self.presenter._lock:
            return [(event["t"], event["p"]) for event in self.presenter._pending.values()]

    def test_card_updates_coalesce_and_land_after_structural_events(self) -> None:
        self.presenter.emit("card", {"key": "1:0", "progress": 0.1})
        self.presenter.emit("cards_add", {"items": [{"key": "1:0"}]})
        self.presenter.emit("card", {"key": "1:0", "progress": 0.5})
        self.presenter.emit("card", {"key": "1:1", "progress": 0.2})
        self.assertEqual(
            self.pending(),
            [
                ("cards_add", {"items": [{"key": "1:0"}]}),
                ("card", {"key": "1:0", "progress": 0.5}),
                ("card", {"key": "1:1", "progress": 0.2}),
            ],
        )

    def test_logs_are_never_coalesced_but_progress_is(self) -> None:
        for value in (0.1, 0.2, 0.3):
            self.presenter.emit("progress", {"value": value})
        self.presenter.emit("log", {"text": "a"})
        self.presenter.emit("log", {"text": "a"})
        self.assertEqual(
            self.pending(),
            [("progress", {"value": 0.3}), ("log", {"text": "a"}), ("log", {"text": "a"})],
        )

    def test_page_ready_drops_events_covered_by_the_boot_snapshot(self) -> None:
        self.presenter.emit("log", {"text": "startup"})
        self.presenter.page_ready()
        self.assertEqual(self.pending(), [])

    def test_dialogs_resolve_by_id_and_close_answers_pending_with_none(self) -> None:
        first = self.presenter.request("confirm", {"title": "A"})
        second = self.presenter.request("confirm", {"title": "B"})
        dialogs = [payload for event, payload in self.pending() if event == "dialog"]
        self.assertEqual([d["title"] for d in dialogs], ["A", "B"])
        self.presenter.resolve(dialogs[0]["id"], True)
        self.assertTrue(first.result(timeout=1))
        self.presenter.close()
        self.assertIsNone(second.result(timeout=1))
        late = self.presenter.request("confirm", {"title": "C"})
        self.assertIsNone(late.result(timeout=1))


class TestControllerIntents(unittest.TestCase):
    def setUp(self) -> None:
        self.view = _RecordingView()
        self.app = _controller(self.view)

    def _show(self, entries) -> None:
        self.app.ui_queue.put(("results", entries))
        self.app.process_ui_queue()

    def test_results_publish_reset_header_and_cards(self) -> None:
        self._show(
            [
                {"title": "One", "webpage_url": "https://example.com/1", "duration": 75, "uploader": "A"},
                {"title": "Two", "webpage_url": "https://example.com/2", "playlist_title": "Mix"},
            ]
        )
        self.assertTrue(self.view.named("results_reset"))
        header = self.view.named("results")[-1]
        self.assertEqual((header["total"], header["collection"]), (2, "Mix"))
        items = self.view.named("cards_add")[-1]["items"]
        self.assertEqual([item["title"] for item in items], ["One", "Two"])
        self.assertEqual(items[0]["duration"], "1:15")
        self.assertTrue(self.view.named("batch")[-1]["visible"])

    def test_single_result_hides_batch_controls(self) -> None:
        self._show([{"title": "Solo", "webpage_url": "https://example.com/1"}])
        self.assertFalse(self.view.named("batch")[-1]["visible"])

    def test_card_intents_route_to_the_live_card_and_ignore_stale_generations(self) -> None:
        self._show(
            [
                {"title": "One", "webpage_url": "https://example.com/1"},
                {"title": "Two", "webpage_url": "https://example.com/2"},
            ]
        )
        generation = self.app.entries_generation
        self.app.card_intent(generation, 1, "select", False)
        self.assertEqual(self.app.selected_entry_indexes, {0})
        self.app.card_intent(generation, 1, "format", "bestaudio")
        self.assertEqual(self.app.entry_format_by_index, {1: "bestaudio"})
        self.app.card_intent(generation - 1, 0, "select", False)
        self.assertEqual(self.app.selected_entry_indexes, {0})
        card = self.app.card_by_index[1]
        card.on_download = mock.Mock()
        self.app.card_intent(generation, 1, "download")
        card.on_download.assert_called_once_with(card, "bestaudio")

    def test_load_more_renders_the_next_page_only_while_entries_remain(self) -> None:
        self.app.batch_size = 2
        self._show([{"title": f"T{i}", "webpage_url": f"https://example.com/{i}"} for i in range(3)])
        self.assertEqual(self.app.rendered_count, 2)
        self.app.load_more()
        self.app.load_more()
        self.assertEqual(self.app.rendered_count, 3)
        self.assertEqual(len(self.view.named("cards_add")), 2)

    def test_fetch_normalizes_and_publishes_the_url(self) -> None:
        with mock.patch.object(self.app.executor, "submit") as submit:
            self.app.fetch_videos("  https://example.com/watch?v=1  ")
        submit.assert_called_once()
        self.assertEqual(self.app.url_entry.get(), "https://example.com/watch?v=1")
        self.assertEqual(self.view.named("url")[-1]["value"], "https://example.com/watch?v=1")
        self.assertTrue(self.view.named("fetching")[-1]["busy"])

    def test_dispatcher_keeps_draining_while_a_dialog_is_open(self) -> None:
        drained = []
        future: Future = Future()
        self.app.view.request = lambda kind, payload: future
        self.app.ui_queue.put(("call", lambda: drained.append("progress")))
        self.app.ui_queue.put(("call", lambda: future.set_result(True)))
        self.assertTrue(self.app.ui_confirm("Format unavailable", "Retry?"))
        self.assertEqual(drained, ["progress"])

    def test_worker_dialogs_block_only_the_worker(self) -> None:
        self.view.answer = "fallback"
        result = {}
        worker = threading.Thread(target=lambda: result.update(choice=self.app.ui_ffmpeg_fallback("Best")))
        worker.start()
        worker.join(timeout=2)
        self.assertEqual(result, {"choice": "fallback"})
        self.view.answer = None
        self.assertEqual(self.app.ui_ffmpeg_fallback("Best"), "cancel")

    def test_credentials_dialog_returns_trimmed_pair_or_nothing(self) -> None:
        self.view.answer = {"username": "  me@example.com ", "password": "pw"}
        self.assertEqual(
            self.app.prompt_for_credentials("https://www.example.com/v"),
            ("me@example.com", "pw"),
        )
        self.assertEqual(self.view.requests[-1][1]["site"], "example.com")
        self.view.answer = {"username": "me", "password": ""}
        self.assertEqual(self.app.prompt_for_credentials("https://example.com/v"), (None, None))

    def test_output_folder_and_cookie_file_settings_publish(self) -> None:
        with mock.patch("doggybag.app.controller.store_preferred_cookies_path"):
            self.app.set_cookies_file("C:/cookies.txt")
            self.app.set_cookies_file(None)
        self.app.set_output_folder("D:/Media")
        settings = self.view.named("settings")[-1]
        self.assertEqual(settings["output_path"], "D:/Media")
        self.assertIsNone(settings["cookies_path"])

    def test_boot_state_lists_formats_models_and_browsers(self) -> None:
        boot = self.app.boot_state()
        self.assertEqual(boot["whisper_models"], ["small"])
        self.assertEqual([b["id"] for b in boot["cookie_browsers"]], ["brave", "chrome", "edge", "firefox"])
        self.assertTrue(all({"id", "label"} <= set(f) for f in boot["formats"]))


class TestEntryDetails(unittest.TestCase):
    def test_only_http_thumbnails_and_pages_are_exposed(self) -> None:
        details = entry_details(
            {
                "extractor_key": "Youtube",
                "thumbnail": "file:///etc/passwd",
                "webpage_url": "javascript:alert(1)",
                "_ffmpeg_headers": {"Referer": "x"},
            }
        )
        self.assertEqual(details["site"], "YouTube")
        self.assertIsNone(details["thumbnail"])
        self.assertIsNone(details["webpage_url"])
        self.assertTrue(details["direct"])

    def test_generic_extractor_falls_back_to_host(self) -> None:
        details = entry_details({"extractor": "generic", "webpage_url": "https://www.example.org/a"})
        self.assertEqual(details["site"], "example.org")


class TestBridgeSafety(unittest.TestCase):
    def test_open_output_only_accepts_files_the_app_produced(self) -> None:
        presenter = WebViewPresenter()
        self.addCleanup(presenter.close)
        ready = threading.Event()
        ready.set()
        bridge = Bridge(presenter, ready)
        card = mock.Mock(outputs=["C:/out/a.mp4"])
        bridge._attach(mock.Mock(cards=[card]))
        with mock.patch("doggybag.ui.webview_app.desktop") as desktop:
            self.assertFalse(bridge.open_output("C:/Windows/system32/calc.exe"))
            bridge.open_output("C:/out/a.mp4", True)
        desktop.reveal_path.assert_called_once_with("C:/out/a.mp4")
        desktop.open_path.assert_not_called()

    def test_open_url_rejects_non_http_schemes(self) -> None:
        bridge = Bridge(mock.Mock(), threading.Event())
        with mock.patch("doggybag.ui.webview_app.webbrowser") as browser:
            self.assertFalse(bridge.open_url("file:///C:/x"))
            self.assertTrue(bridge.open_url("https://example.com"))
        browser.open.assert_called_once_with("https://example.com")

    def test_bridge_exposes_no_public_attributes(self) -> None:
        bridge = Bridge(mock.Mock(), threading.Event())
        public = [name for name, value in vars(bridge).items() if not name.startswith("_")]
        self.assertEqual(public, [])


if __name__ == "__main__":
    unittest.main()
