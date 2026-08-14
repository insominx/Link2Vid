"""Phase 2/3 Whisper workflow: verified-media, queue protocol, acquisition, cookies."""

from __future__ import annotations

import inspect
import queue
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from link2vid.core.audio_source import AudioAcquisitionError, AudioSourceResolver, SourceKind
from link2vid.core.diagnostics import WhisperDiagnosticsInput, build_diagnostics
from link2vid.core.downloader import DownloadManager
from link2vid.core.errors import CookiesRequiredError
from link2vid.core.whisper_transcription import (
    TranscriptionRequest,
    TranscriptionResult,
    VerifiedMedia,
    WhisperPhase,
    WhisperRunSnapshot,
    WhisperService,
)
from link2vid.ui.main_window import VideoDownloaderApp


class _FakeCard:
    def __init__(self, name: str = "card") -> None:
        self.name = name
        self._exists = True
        self.status = None
        self.state = None
        self.progress = None
        self.progress_mode = "determinate"
        self.actions_enabled = None

    def winfo_exists(self) -> bool:
        return self._exists

    def destroy(self) -> None:
        self._exists = False

    def set_status(self, status, state=None):
        self.status = status
        self.state = state

    def set_progress(self, value):
        self.progress_mode = "determinate"
        self.progress = value

    def set_progress_mode(self, mode: str) -> None:
        normalized = str(mode).strip().lower()
        if normalized not in ("determinate", "indeterminate"):
            raise ValueError(f"Unsupported progress mode: {mode!r}")
        self.progress_mode = normalized

    def reset_progress(self) -> None:
        self.progress_mode = "determinate"
        self.progress = 0

    def get_progress_mode(self) -> str:
        return self.progress_mode

    def set_actions_enabled(self, enabled):
        self.actions_enabled = enabled

    def set_thumbnail_image(self, image):
        self.thumbnail = image

    def pack(self, **kwargs):
        return None


class _EqualFakeCard(_FakeCard):
    def __eq__(self, other) -> bool:
        return isinstance(other, _EqualFakeCard) and self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)


def _write_media(path: Path, content: bytes = b"media-bytes") -> VerifiedMedia:
    path.write_bytes(content)
    stat = path.stat()
    return VerifiedMedia(path=path, size=stat.st_size, modified_at=stat.st_mtime)


def _snapshot(
    request_id: str,
    *,
    phase: WhisperPhase = WhisperPhase.LOADING_MODEL,
    model_name: str = "small",
) -> WhisperRunSnapshot:
    return WhisperRunSnapshot(
        request_id=request_id,
        phase=phase,
        model_name=model_name,
        cache_root="C:\\models",
        effective_device=None,
        fallback_used=False,
        source_kind=None,
        error_reason=None,
        updated_at=time.time(),
    )


class _AppHarness(unittest.TestCase):
    """Minimal VideoDownloaderApp stand-in for queue/association tests."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tmp.name)
        self.app = object.__new__(VideoDownloaderApp)
        self.app.main_thread_id = threading.get_ident()
        self.app.ui_queue = queue.Queue()
        self.app.card_entries = {}
        self.app.video_entries = []
        self.app.entries_generation = 0
        self.app.selected_entry_indexes = set()
        self.app.entry_format_by_index = {}
        self.app.entry_transcript_by_index = {}
        self.app.card_index_by_card = {}
        self.app.card_by_index = {}
        self.app.batch_run = None
        self.app.batch_prompt_policy = None
        self.app.batch_bar = mock.Mock()
        self.app.verified_media_by_card = {}
        self.app._verified_media_epoch_by_card = {}
        self.app.whisper_runs_by_card = {}
        self.app.whisper_request_id_by_card = {}
        self.app.latest_whisper_card = None
        self.app.whisper_models = []
        self.app._whisper_cookie_retry_lock = threading.Lock()
        self.app.busy_cards = set()
        self.app.cards = []
        self.app.output_path = str(self.out_dir)
        self.app.log_history = []
        self.app.last_error_reason = None
        self.app.format_label_map = {"best": "Best (single file)"}
        self.app.progress = mock.Mock()
        self.app.progress_bar = mock.Mock()
        self.app.download_manager = mock.Mock()
        self.app.download_manager.last_cookies_mode = "none"
        self.app.download_manager.last_cookies_browser = None
        self.app.download_manager.last_js_runtime = None
        self.app.download_manager.last_js_runtime_path = None
        self.app.download_manager.last_remote_components = []
        self.app.whisper_service = WhisperService(
            available_models_fn=lambda: ["tiny", "base", "small"]
        )
        self.app.audio_source_resolver = AudioSourceResolver(
            download_manager=self.app.download_manager
        )
        self.app.root = mock.Mock()

        # Bind real methods under test.
        for name in (
            "_card_in_session",
            "_next_verified_media_epoch",
            "_apply_verified_media_event",
            "invalidate_verified_media",
            "queue_verified_media",
            "_verify_download_outputs",
            "queue_card_status",
            "queue_card_progress",
            "queue_card_busy",
            "_set_card_busy",
            "set_progress",
            "log",
            "log_error",
            "download_video",
            "_download_direct_media",
            "download_transcript",
            "handle_card_transcript",
            "entry_url_for_entry",
            "choose_cookies_browser",
            "set_cookies_browser",
            "_prepare_cookie_retry",
            "confirm_format_fallback",
            "confirm_ffmpeg_fallback",
            "clear_results",
            "_folder_snapshot",
            "_recent_download_outputs",
            "_probe_whisper_models",
            "_log_whisper_safely",
            "_consume_whisper_ui_event",
            "_whisper_event_is_current",
            "_apply_whisper_snapshot_event",
            "_apply_card_progress_mode_event",
            "_apply_whisper_card_progress_event",
            "_apply_whisper_status_event",
            "queue_whisper_snapshot",
            "queue_card_progress_mode",
            "queue_whisper_card_progress",
            "queue_whisper_status",
            "queue_whisper_busy",
            "_apply_whisper_busy_event",
            "handle_card_whisper",
            "run_whisper_transcription",
            "_sanitize_whisper_error",
            "_whisper_phase_status_text",
            "_queue_whisper_worker_event",
            "_publish_whisper_worker_terminal",
            "_log_whisper_worker_event_failure",
            "_cleanup_failed_whisper_start",
            "_best_effort_whisper_start_event",
            "_latest_whisper_diagnostics_input",
            "copy_diagnostics",
            "run_on_ui_thread",
            "process_ui_queue",
        ):
            setattr(self.app, name, getattr(VideoDownloaderApp, name).__get__(self.app))

        # log() on main thread needs _append_log / debug path stubs.
        self.app._append_log = lambda message: self.app.log_history.append(message)
        self.app.debug_log_path = str(self.out_dir / "debug.log")
        self.app._append_debug_log = lambda message: None
        self.app.log_drawer = None
        self.app.url_entry = mock.Mock()
        self.app.url_entry.get.return_value = ""
        self.app.ui_warn = mock.Mock()
        self.app.ui_confirm = mock.Mock(return_value=False)
        self.app.ui_choose_cookies_browser = mock.Mock(return_value=None)
        self.app.ui_select_cookies = mock.Mock()
        self.app.cookies_path = None
        self.app.cookies_browser = None
        self.app.user_settings_path = self.out_dir / "settings.json"
        self.app.footer = None
        self.app.whisper_models = ["tiny", "base", "small"]
        self.app.format_error = lambda stage, err: f"{stage}: {err}"
        self.app.last_action_kind = None
        self.app.last_selected_title = None
        self.app.last_selected_format = None
        self.app.last_transcript_source = None
        self.app.last_transcript_languages = []
        self.app.last_error = None
        self.app.ffmpeg_path = None
        self.app.js_runtimes = []
        self.app.clipboard_text = None
        self.app.root.clipboard_clear = mock.Mock()
        self.app.root.clipboard_append = mock.Mock(
            side_effect=lambda text: setattr(self.app, "clipboard_text", text)
        )
        # log_error appends to history like production for failure assertions.
        def _log_error(stage, err):
            self.app.log_history.append(f"ERROR [{stage}] {type(err).__name__}: {err}")
            self.app.last_error = err

        self.app.log_error = _log_error

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _drain_verified_media_queue(self) -> None:
        while True:
            try:
                action, payload = self.app.ui_queue.get_nowait()
            except queue.Empty:
                return
            if action == "verified_media":
                self.app._apply_verified_media_event(payload)

    def _drain_whisper_queue(self) -> None:
        """Drain Whisper-related queue actions through the real consumer path."""
        # process_ui_queue reschedules itself; stub after and run one drain pass.
        self.app.root.after = mock.Mock()
        self.app.process_ui_queue()

    def _register_card(self, card: _FakeCard | None = None) -> _FakeCard:
        card = card or _FakeCard()
        self.app.card_entries[card] = {
            "title": "Demo",
            "webpage_url": "https://example.com/v",
        }
        self.app.cards.append(card)
        return card


class TestVerifiedMediaAssociation(_AppHarness):
    def test_one_exact_ytdlp_output_associates_verified_media(self) -> None:
        card = self._register_card()
        media_path = self.out_dir / "one.mp4"
        media_path.write_bytes(b"exact-one")
        epoch = self.app.invalidate_verified_media(card)

        def fake_download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            if post_hook:
                post_hook(str(media_path))
            return True

        self.app.download_manager.download.side_effect = fake_download
        self.app.download_video(
            "https://example.com/v",
            "best",
            str(self.out_dir),
            card=card,
            fmt_label="Best",
            media_epoch=epoch,
        )
        self._drain_verified_media_queue()

        associated = self.app.verified_media_by_card[card]
        self.assertIsInstance(associated, VerifiedMedia)
        self.assertEqual(associated.path, media_path)
        self.assertEqual(associated.size, media_path.stat().st_size)
        self.assertEqual(associated.modified_at, media_path.stat().st_mtime)
        self.assertEqual(card.state, "complete")

    def test_ambiguous_multiple_outputs_complete_without_association(self) -> None:
        card = self._register_card()
        first = self.out_dir / "a.mp4"
        second = self.out_dir / "b.mp4"
        first.write_bytes(b"first-file")
        second.write_bytes(b"second-file")
        epoch = self.app.invalidate_verified_media(card)

        def fake_download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            if post_hook:
                post_hook(str(first))
                post_hook(str(second))
            return True

        self.app.download_manager.download.side_effect = fake_download
        self.app.download_video(
            "https://example.com/v",
            "best",
            str(self.out_dir),
            card=card,
            fmt_label="Best",
            media_epoch=epoch,
        )
        self._drain_verified_media_queue()

        self.assertNotIn(card, self.app.verified_media_by_card)
        self.assertEqual(card.state, "complete")

    def test_empty_or_missing_output_does_not_associate(self) -> None:
        card = self._register_card()
        missing = self.out_dir / "missing.mp4"
        empty = self.out_dir / "empty.mp4"
        empty.write_bytes(b"")
        epoch = self.app.invalidate_verified_media(card)

        def fake_download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            if post_hook:
                post_hook(str(missing))
                post_hook(str(empty))
            return True

        self.app.download_manager.download.side_effect = fake_download
        self.app.download_video(
            "https://example.com/v",
            "best",
            str(self.out_dir),
            card=card,
            fmt_label="Best",
            media_epoch=epoch,
        )
        self._drain_verified_media_queue()

        self.assertNotIn(card, self.app.verified_media_by_card)
        self.assertEqual(card.state, "failed")

    def test_recent_unreported_file_can_complete_but_never_associates(self) -> None:
        card = self._register_card()
        unrelated = self.out_dir / "unrelated-recent.mp4"
        unrelated.write_bytes(b"not-produced-by-this-action")
        epoch = self.app.invalidate_verified_media(card)
        self.app.download_manager.download.return_value = True

        with mock.patch.object(
            self.app,
            "queue_verified_media",
            wraps=self.app.queue_verified_media,
        ) as queue_media:
            self.app.download_video(
                "https://example.com/v",
                "best",
                str(self.out_dir),
                card=card,
                fmt_label="Best",
                media_epoch=epoch,
            )

        self._drain_verified_media_queue()
        self.assertEqual(card.state, "complete")
        self.assertNotIn(card, self.app.verified_media_by_card)
        queue_media.assert_not_called()

    def test_direct_hls_verifies_and_associates_final_path(self) -> None:
        card = self._register_card()
        epoch = self.app.invalidate_verified_media(card)
        outfile = self.out_dir / "Demo.mp4"

        def fake_ffmpeg(media_url, output_file, headers, progress_hook=None, duration_seconds=None, **_kwargs):
            Path(output_file).write_bytes(b"hls-bytes")

        with mock.patch("link2vid.ui.main_window.download_with_ffmpeg", side_effect=fake_ffmpeg):
            with mock.patch("link2vid.ui.main_window.unique_output_path", return_value=str(outfile)):
                with mock.patch("threading.Thread") as thread_cls:
                    started = []

                    def capture_thread(target=None, daemon=None):
                        started.append(target)
                        return mock.Mock(start=lambda: target())

                    thread_cls.side_effect = capture_thread
                    self.app._download_direct_media(
                        card,
                        "https://example.com/stream.m3u8",
                        {"Referer": "https://example.com/"},
                        "Demo",
                        media_epoch=epoch,
                    )

        self._drain_verified_media_queue()
        associated = self.app.verified_media_by_card[card]
        self.assertEqual(associated.path, outfile)
        self.assertEqual(associated.size, outfile.stat().st_size)
        self.assertEqual(associated.modified_at, outfile.stat().st_mtime)
        self.assertEqual(card.state, "complete")
        self.assertEqual(card.status, "Complete")

    def test_direct_audio_extract_writes_mp3_and_associates(self) -> None:
        card = self._register_card()
        epoch = self.app.invalidate_verified_media(card)
        seen = {}

        def fake_ffmpeg(media_url, output_file, headers, progress_hook=None, duration_seconds=None, **kwargs):
            seen["output_file"] = output_file
            seen["audio_only"] = kwargs.get("audio_only")
            Path(output_file).write_bytes(b"mp3-bytes")

        with mock.patch("link2vid.ui.main_window.download_with_ffmpeg", side_effect=fake_ffmpeg):
            with mock.patch("threading.Thread") as thread_cls:
                def capture_thread(target=None, daemon=None):
                    return mock.Mock(start=lambda: target())

                thread_cls.side_effect = capture_thread
                self.app._download_direct_media(
                    card,
                    "https://example.com/stream.m3u8",
                    {"Referer": "https://example.com/"},
                    "Demo",
                    media_epoch=epoch,
                    audio_only=True,
                )

        self._drain_verified_media_queue()
        expected = self.out_dir / "Demo.mp3"
        self.assertTrue(seen["audio_only"])
        self.assertEqual(Path(seen["output_file"]), expected)
        associated = self.app.verified_media_by_card[card]
        self.assertEqual(associated.path, expected)
        self.assertEqual(card.state, "complete")

    def test_direct_hls_missing_file_fails_without_association(self) -> None:
        card = self._register_card()
        epoch = self.app.invalidate_verified_media(card)
        outfile = self.out_dir / "missing-hls.mp4"

        def fake_ffmpeg(media_url, output_file, headers, progress_hook=None, duration_seconds=None, **_kwargs):
            return None

        with mock.patch("link2vid.ui.main_window.download_with_ffmpeg", side_effect=fake_ffmpeg):
            with mock.patch("link2vid.ui.main_window.unique_output_path", return_value=str(outfile)):
                with mock.patch("threading.Thread") as thread_cls:
                    def run_inline(target=None, daemon=None):
                        return mock.Mock(start=lambda: target())

                    thread_cls.side_effect = run_inline
                    self.app._download_direct_media(
                        card,
                        "https://example.com/stream.m3u8",
                        {},
                        "Demo",
                        media_epoch=epoch,
                    )

        self._drain_verified_media_queue()
        self.assertNotIn(card, self.app.verified_media_by_card)
        self.assertEqual(card.state, "failed")

    def test_stale_destroyed_card_event_is_dropped(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "stale.mp4")
        epoch = self.app.invalidate_verified_media(card)
        card.destroy()
        self.app.card_entries.pop(card, None)

        self.app.queue_verified_media(card, media, epoch)
        self._drain_verified_media_queue()
        self.assertEqual(self.app.verified_media_by_card, {})

    def test_clear_results_clears_verified_media_map(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "clear.mp4")
        epoch = self.app.invalidate_verified_media(card)
        self.app.queue_verified_media(card, media, epoch)
        self.assertIn(card, self.app.verified_media_by_card)

        # clear_results destroys widgets and clears maps; stub pack_forget path.
        self.app.load_more_button = mock.Mock()
        self.app.load_more_button.winfo_manager.return_value = ""
        self.app.clear_results()

        self.assertEqual(self.app.verified_media_by_card, {})
        self.assertEqual(self.app._verified_media_epoch_by_card, {})
        self.assertEqual(self.app.card_entries, {})
        self.assertFalse(card.winfo_exists())

    def test_prior_association_invalidated_before_new_media_action(self) -> None:
        card = self._register_card()
        old = _write_media(self.out_dir / "old.mp4", b"old")
        first_epoch = self.app.invalidate_verified_media(card)
        self.app.queue_verified_media(card, old, first_epoch)
        self.assertEqual(self.app.verified_media_by_card[card], old)

        second_epoch = self.app.invalidate_verified_media(card)
        self.assertNotIn(card, self.app.verified_media_by_card)
        self.assertEqual(second_epoch, first_epoch + 1)

        # Late association from the superseded download must not restore stale media.
        self.app.queue_verified_media(card, old, first_epoch)
        self._drain_verified_media_queue()
        self.assertNotIn(card, self.app.verified_media_by_card)

        new = _write_media(self.out_dir / "new.mp4", b"new-bytes")
        self.app.queue_verified_media(card, new, second_epoch)
        self._drain_verified_media_queue()
        self.assertEqual(self.app.verified_media_by_card[card], new)

    def test_failed_media_action_leaves_prior_association_invalidated(self) -> None:
        card = self._register_card()
        old = _write_media(self.out_dir / "prior.mp4", b"prior")
        old_epoch = self.app.invalidate_verified_media(card)
        self.app.queue_verified_media(card, old, old_epoch)
        self.assertEqual(self.app.verified_media_by_card[card], old)

        failed_epoch = self.app.invalidate_verified_media(card)
        self.app.download_manager.download.return_value = False
        self.app.download_video(
            "https://example.com/v",
            "best",
            str(self.out_dir),
            card=card,
            fmt_label="Best",
            media_epoch=failed_epoch,
        )
        self._drain_verified_media_queue()

        self.assertNotIn(card, self.app.verified_media_by_card)
        self.assertEqual(card.state, "failed")

    def test_stale_old_epoch_terminal_events_preserve_newer_association(self) -> None:
        card = self._register_card()
        old = _write_media(self.out_dir / "old-terminal.mp4", b"old")
        old_epoch = self.app.invalidate_verified_media(card)

        new = _write_media(self.out_dir / "new-terminal.mp4", b"new")
        new_epoch = self.app.invalidate_verified_media(card)
        self.app.queue_verified_media(card, new, new_epoch)
        self.assertEqual(self.app.verified_media_by_card[card], new)

        self.app._apply_verified_media_event((card, old, old_epoch))
        self.app._apply_verified_media_event((card, None, old_epoch))

        self.assertEqual(self.app.verified_media_by_card[card], new)

    def test_equal_but_different_card_is_rejected_by_identity(self) -> None:
        real_card = self._register_card(_EqualFakeCard("same"))
        impostor = _EqualFakeCard("same")
        self.assertEqual(real_card, impostor)
        self.assertIsNot(real_card, impostor)

        real_media = _write_media(self.out_dir / "real-card.mp4", b"real")
        epoch = self.app.invalidate_verified_media(real_card)
        self.app.queue_verified_media(real_card, real_media, epoch)
        self.assertEqual(self.app.verified_media_by_card[real_card], real_media)

        impostor_media = _write_media(self.out_dir / "impostor.mp4", b"impostor")
        self.app._apply_verified_media_event((impostor, impostor_media, epoch))

        self.assertEqual(self.app.verified_media_by_card[real_card], real_media)
        self.assertTrue(self.app._card_in_session(real_card))
        self.assertFalse(self.app._card_in_session(impostor))

    def test_verify_captures_exact_size_and_mtime(self) -> None:
        path = self.out_dir / "capture.mp4"
        path.write_bytes(b"12345")
        verified = self.app._verify_download_outputs([str(path)])
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0].path, path)
        self.assertEqual(verified[0].size, 5)
        self.assertEqual(verified[0].modified_at, path.stat().st_mtime)

    def test_provider_transcript_does_not_associate_media(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "keep.mp4")
        epoch = self.app.invalidate_verified_media(card)
        self.app.queue_verified_media(card, media, epoch)
        before = self.app.verified_media_by_card[card]

        result = mock.Mock(source="manual", languages=["en"])
        self.app.download_manager.download_transcript.return_value = result

        # Transcript worker must not enqueue verified_media events.
        with mock.patch.object(self.app, "queue_verified_media") as queue_media:
            self.app.download_transcript(
                "https://example.com/v",
                str(self.out_dir),
                card=card,
            )
            queue_media.assert_not_called()

        self.assertIs(self.app.verified_media_by_card[card], before)
        self.assertEqual(card.state, "complete")
        self.assertIn("Transcript saved", card.status)

    def test_queue_action_shape_is_verified_media_tuple(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "shape.mp4")
        epoch = self.app.invalidate_verified_media(card)

        # Force worker-thread enqueue path.
        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_verified_media(card, media, epoch)
        action, payload = self.app.ui_queue.get_nowait()
        self.assertEqual(action, "verified_media")
        self.assertEqual(payload, (card, media, epoch))

        self.app.main_thread_id = threading.get_ident()
        self.app._apply_verified_media_event(payload)
        self.assertEqual(self.app.verified_media_by_card[card], media)

    def test_invalidate_must_run_on_ui_thread(self) -> None:
        card = self._register_card()
        self.app.main_thread_id = threading.get_ident() + 1
        with self.assertRaises(RuntimeError):
            self.app.invalidate_verified_media(card)


class TestPhase2CSeam(_AppHarness):
    def test_verified_media_by_card_readable_at_request_freeze_time(self) -> None:
        """Phase 2C seam: handle_card_whisper freezes map[card] into TranscriptionRequest."""
        card = self._register_card()
        media = _write_media(self.out_dir / "freeze.mp4")
        epoch = self.app.invalidate_verified_media(card)
        self.app.queue_verified_media(card, media, epoch)

        # Request-freeze reads the current association without mutating it.
        frozen = self.app.verified_media_by_card.get(card)
        self.assertIs(frozen, media)
        self.assertTrue(frozen.is_current())


class TestPhase2C1OwnershipAndProbe(_AppHarness):
    def test_constructor_accepts_injectable_service_and_resolver(self) -> None:
        sig = inspect.signature(VideoDownloaderApp.__init__)
        params = sig.parameters
        self.assertIn("whisper_service", params)
        self.assertIn("audio_source_resolver", params)
        self.assertEqual(params["whisper_service"].default, None)
        self.assertEqual(params["audio_source_resolver"].default, None)
        # Positional root remains the only required arg for existing callers.
        self.assertEqual(params["root"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)

    def test_constructor_owns_supplied_instances_without_display(self) -> None:
        root = mock.MagicMock()
        service = mock.Mock(spec=WhisperService)
        service.list_available_models.return_value = ["tiny", "small"]
        resolver = mock.Mock(spec=AudioSourceResolver)
        widget_factory = mock.Mock(side_effect=lambda *args, **kwargs: mock.MagicMock())
        executor = mock.Mock()

        with ExitStack() as stack:
            for widget_name in (
                "CTkFrame",
                "CTkLabel",
                "CTkEntry",
                "CTkButton",
                "CTkScrollableFrame",
                "CTkProgressBar",
                "DoubleVar",
            ):
                stack.enter_context(
                    mock.patch(
                        f"link2vid.ui.main_window.ctk.{widget_name}",
                        widget_factory,
                    )
                )
            stack.enter_context(
                mock.patch("link2vid.ui.main_window.FooterBar", widget_factory)
            )
            stack.enter_context(
                mock.patch("link2vid.ui.main_window.BatchBar", widget_factory)
            )
            stack.enter_context(
                mock.patch("link2vid.ui.main_window.LogDrawer", widget_factory)
            )
            stack.enter_context(
                mock.patch(
                    "link2vid.ui.main_window.ThreadPoolExecutor",
                    return_value=executor,
                )
            )
            stack.enter_context(mock.patch("link2vid.ui.main_window.DownloadManager"))
            stack.enter_context(mock.patch("link2vid.ui.main_window.VideoFetcher"))
            stack.enter_context(mock.patch("link2vid.ui.main_window.ThumbnailLoader"))
            stack.enter_context(
                mock.patch("link2vid.ui.main_window.get_format_options", return_value=[])
            )
            stack.enter_context(
                mock.patch("link2vid.ui.main_window.get_yt_dlp_version", return_value="test")
            )
            stack.enter_context(
                mock.patch(
                    "link2vid.core.runtime.startup_summary",
                    return_value="test runtime",
                )
            )
            stack.enter_context(
                mock.patch.object(
                    VideoDownloaderApp,
                    "load_dev_defaults",
                    return_value={},
                )
            )
            stack.enter_context(
                mock.patch.object(
                    VideoDownloaderApp,
                    "get_default_output_path",
                    return_value=str(self.out_dir),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    VideoDownloaderApp,
                    "_default_debug_log_path",
                    return_value=str(self.out_dir / "debug.log"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    VideoDownloaderApp,
                    "_populate_url_from_clipboard_if_valid",
                )
            )
            stack.enter_context(
                mock.patch.object(VideoDownloaderApp, "update_button_states")
            )
            stack.enter_context(mock.patch.object(VideoDownloaderApp, "check_ffmpeg"))
            stack.enter_context(mock.patch.object(VideoDownloaderApp, "check_js_runtime"))

            app = VideoDownloaderApp(
                root,
                whisper_service=service,
                audio_source_resolver=resolver,
            )

        self.assertIs(app.whisper_service, service)
        self.assertIs(app.audio_source_resolver, resolver)
        self.assertEqual(app.whisper_models, ["tiny", "small"])
        self.assertEqual(app.whisper_runs_by_card, {})
        self.assertEqual(app.whisper_request_id_by_card, {})
        self.assertIsNone(app.latest_whisper_card)
        service.list_available_models.assert_called_once_with()

    def test_service_construction_does_not_eagerly_import_native_deps(self) -> None:
        probe_calls = []

        def boom():
            probe_calls.append(1)
            raise RuntimeError("native import should stay lazy")

        service = WhisperService(available_models_fn=boom)
        self.assertIsNone(service.loaded_slot)
        self.assertEqual(probe_calls, [])

        resolver = AudioSourceResolver(download_manager=object())
        self.assertIs(resolver._download_manager.__class__, object)

    def test_probe_success_records_exact_aliases(self) -> None:
        aliases = ["tiny", "base", "small", "medium"]
        self.app.whisper_service = mock.Mock(spec=WhisperService)
        self.app.whisper_service.list_available_models.return_value = aliases
        self.app._probe_whisper_models()
        self.assertEqual(self.app.whisper_models, aliases)
        self.app.whisper_service.list_available_models.assert_called_once_with()
        self.assertTrue(any("Whisper models available" in msg for msg in self.app.log_history))

    def test_success_log_failure_preserves_fetched_aliases(self) -> None:
        aliases = ["tiny", "small"]
        self.app.whisper_service = mock.Mock(spec=WhisperService)
        self.app.whisper_service.list_available_models.return_value = aliases
        self.app.log = mock.Mock(side_effect=RuntimeError("log widget unavailable"))

        self.app._probe_whisper_models()

        self.assertEqual(self.app.whisper_models, aliases)
        self.app.whisper_service.list_available_models.assert_called_once_with()
        self.assertEqual(self.app.log.call_count, 1)

    def test_probe_failure_isolates_to_empty_alias_list(self) -> None:
        def boom():
            raise ImportError("faster_whisper missing")

        self.app.whisper_service = WhisperService(available_models_fn=boom)
        self.app.whisper_models = ["stale"]
        self.app._probe_whisper_models()
        self.assertEqual(self.app.whisper_models, [])
        self.assertTrue(
            any("Whisper unavailable" in msg for msg in self.app.log_history)
        )

    def test_per_app_injection_is_owned_not_shared(self) -> None:
        service_a = WhisperService(available_models_fn=lambda: ["tiny"])
        service_b = WhisperService(available_models_fn=lambda: ["small"])
        resolver_a = AudioSourceResolver()
        resolver_b = AudioSourceResolver()

        app_a = object.__new__(VideoDownloaderApp)
        app_b = object.__new__(VideoDownloaderApp)
        app_a.whisper_service = service_a
        app_a.audio_source_resolver = resolver_a
        app_b.whisper_service = service_b
        app_b.audio_source_resolver = resolver_b

        self.assertIs(app_a.whisper_service, service_a)
        self.assertIs(app_b.whisper_service, service_b)
        self.assertIsNot(app_a.whisper_service, app_b.whisper_service)
        self.assertIsNot(app_a.audio_source_resolver, app_b.audio_source_resolver)


class TestPhase2C1QueueProtocol(_AppHarness):
    def test_snapshot_and_progress_mode_applied_by_consumer(self) -> None:
        card = self._register_card()
        request_id = "req-apply-1"
        self.app.whisper_request_id_by_card[card] = request_id
        snap = _snapshot(request_id, phase=WhisperPhase.TRANSCRIBING)

        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_whisper_snapshot(card, request_id, snap)
        self.app.queue_card_progress_mode(card, request_id, "indeterminate")
        self.app.queue_whisper_status(card, request_id, "Transcribing", "downloading")
        self.assertEqual(self.app.whisper_runs_by_card, {})
        self.assertEqual(card.progress_mode, "determinate")
        self.assertIsNone(card.status)

        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()

        self.assertIs(self.app.whisper_runs_by_card[card], snap)
        self.assertEqual(card.progress_mode, "indeterminate")
        self.assertEqual(card.status, "Transcribing")
        self.assertEqual(card.state, "downloading")

    def test_progress_mode_supports_determinate_indeterminate_and_reset(self) -> None:
        card = self._register_card()
        request_id = "req-mode"
        self.app.whisper_request_id_by_card[card] = request_id

        self.app.queue_card_progress_mode(card, request_id, "indeterminate")
        self.assertEqual(card.progress_mode, "indeterminate")

        self.app.queue_card_progress_mode(card, request_id, "determinate")
        self.assertEqual(card.progress_mode, "determinate")

        card.progress = 0.4
        self.app.queue_card_progress_mode(card, request_id, "reset")
        self.assertEqual(card.progress_mode, "determinate")
        self.assertEqual(card.progress, 0)

    def test_whisper_runs_map_mutates_only_in_consumer(self) -> None:
        card = self._register_card()
        request_id = "req-sot"
        self.app.whisper_request_id_by_card[card] = request_id
        snap = _snapshot(request_id)

        # Worker path only enqueues; map stays empty until consumer runs.
        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_whisper_snapshot(card, request_id, snap)
        self.assertNotIn(card, self.app.whisper_runs_by_card)

        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()
        self.assertIs(self.app.whisper_runs_by_card[card], snap)

    def test_request_id_mismatch_drops_events(self) -> None:
        card = self._register_card()
        current = "req-current"
        stale = "req-stale"
        self.app.whisper_request_id_by_card[card] = current
        current_snap = _snapshot(current, phase=WhisperPhase.COMPLETE)
        self.app.whisper_runs_by_card[card] = current_snap

        self.app.queue_whisper_snapshot(card, stale, _snapshot(stale))
        self.app.queue_card_progress_mode(card, stale, "indeterminate")
        self.app.queue_whisper_status(card, stale, "Stale", "failed")

        self.assertIs(self.app.whisper_runs_by_card[card], current_snap)
        self.assertEqual(card.progress_mode, "determinate")
        self.assertIsNone(card.status)

    def test_equal_but_different_card_identity_is_rejected(self) -> None:
        real = self._register_card(_EqualFakeCard("same"))
        impostor = _EqualFakeCard("same")
        self.assertEqual(real, impostor)
        self.assertIsNot(real, impostor)

        request_id = "req-identity"
        self.app.whisper_request_id_by_card[real] = request_id
        real_snap = _snapshot(request_id)
        impostor_snap = _snapshot(request_id, phase=WhisperPhase.FAILED)

        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_whisper_snapshot(real, request_id, real_snap)
        self.app.queue_whisper_snapshot(impostor, request_id, impostor_snap)
        self.app.queue_card_progress_mode(impostor, request_id, "indeterminate")
        self.assertEqual(self.app.whisper_runs_by_card, {})

        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()
        self.assertIs(self.app.whisper_runs_by_card[real], real_snap)
        self.assertTrue(self.app._card_in_session(real))
        self.assertFalse(self.app._card_in_session(impostor))
        # Dict membership uses __eq__; assert no identity key for the impostor.
        self.assertFalse(any(key is impostor for key in self.app.whisper_runs_by_card))
        self.assertEqual(real.progress_mode, "determinate")

    def test_widget_fault_does_not_prevent_next_queue_event(self) -> None:
        faulty = self._register_card(_FakeCard("faulty"))
        healthy = self._register_card(_FakeCard("healthy"))
        faulty_request = "req-faulty"
        healthy_request = "req-healthy"
        self.app.whisper_request_id_by_card[faulty] = faulty_request
        self.app.whisper_request_id_by_card[healthy] = healthy_request
        faulty.set_progress_mode = mock.Mock(
            side_effect=RuntimeError("widget was destroyed during update")
        )
        healthy_snapshot = _snapshot(
            healthy_request,
            phase=WhisperPhase.TRANSCRIBING,
        )

        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_card_progress_mode(faulty, faulty_request, "indeterminate")
        self.app.queue_whisper_snapshot(healthy, healthy_request, healthy_snapshot)
        self.app.queue_whisper_status(
            healthy,
            healthy_request,
            "Transcribing",
            "downloading",
        )

        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()

        faulty.set_progress_mode.assert_called_once_with("indeterminate")
        self.assertIs(self.app.whisper_runs_by_card[healthy], healthy_snapshot)
        self.assertEqual(healthy.status, "Transcribing")
        self.assertEqual(healthy.state, "downloading")
        self.assertTrue(
            any("Dropped card_progress_mode event" in msg for msg in self.app.log_history)
        )

    def test_status_widget_fault_does_not_prevent_next_queue_event(self) -> None:
        faulty = self._register_card(_FakeCard("faulty-status"))
        healthy = self._register_card(_FakeCard("healthy-status"))
        faulty_request = "req-faulty-status"
        healthy_request = "req-healthy-status"
        self.app.whisper_request_id_by_card[faulty] = faulty_request
        self.app.whisper_request_id_by_card[healthy] = healthy_request
        faulty.set_status = mock.Mock(side_effect=RuntimeError("status widget gone"))

        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_whisper_status(
            faulty,
            faulty_request,
            "Loading model",
            "downloading",
        )
        self.app.queue_card_progress_mode(healthy, healthy_request, "indeterminate")

        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()

        faulty.set_status.assert_called_once_with(
            "Loading model",
            state="downloading",
        )
        self.assertEqual(healthy.progress_mode, "indeterminate")
        self.assertTrue(
            any("Dropped whisper_status event" in msg for msg in self.app.log_history)
        )

    def test_snapshot_half_live_card_does_not_prevent_next_queue_event(self) -> None:
        half_live = self._register_card(_FakeCard("half-live"))
        healthy = self._register_card(_FakeCard("healthy-after-snapshot"))
        half_live_request = "req-half-live"
        healthy_request = "req-after-half-live"
        self.app.whisper_request_id_by_card[half_live] = half_live_request
        self.app.whisper_request_id_by_card[healthy] = healthy_request
        half_live.winfo_exists = mock.Mock(
            side_effect=RuntimeError("Tk widget command no longer exists")
        )

        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_whisper_snapshot(
            half_live,
            half_live_request,
            _snapshot(half_live_request),
        )
        self.app.queue_card_progress_mode(
            healthy,
            healthy_request,
            "indeterminate",
        )

        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()

        self.assertFalse(any(key is half_live for key in self.app.whisper_runs_by_card))
        self.assertEqual(healthy.progress_mode, "indeterminate")
        self.assertTrue(
            any("card widget is unavailable" in msg for msg in self.app.log_history)
        )

    def test_destroyed_card_events_are_dropped(self) -> None:
        card = self._register_card()
        request_id = "req-destroyed"
        self.app.whisper_request_id_by_card[card] = request_id
        card.destroy()
        self.app.card_entries.pop(card, None)

        self.app.queue_whisper_snapshot(card, request_id, _snapshot(request_id))
        self.app.queue_card_progress_mode(card, request_id, "indeterminate")
        self.assertEqual(self.app.whisper_runs_by_card, {})

    def test_clear_results_clears_whisper_session_state(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "clear-whisper.mp4")
        epoch = self.app.invalidate_verified_media(card)
        self.app.queue_verified_media(card, media, epoch)
        request_id = "req-clear"
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.whisper_runs_by_card[card] = _snapshot(request_id)
        self.app.latest_whisper_card = card

        self.app.load_more_button = mock.Mock()
        self.app.load_more_button.winfo_manager.return_value = ""
        self.app.clear_results()

        self.assertEqual(self.app.verified_media_by_card, {})
        self.assertEqual(self.app.whisper_runs_by_card, {})
        self.assertEqual(self.app.whisper_request_id_by_card, {})
        self.assertIsNone(self.app.latest_whisper_card)
        self.assertEqual(self.app.card_entries, {})
        self.assertFalse(card.winfo_exists())

    def test_late_events_after_clear_are_ignored(self) -> None:
        card = self._register_card()
        request_id = "req-late"
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.latest_whisper_card = card

        self.app.main_thread_id = threading.get_ident() + 1
        snap = _snapshot(request_id, phase=WhisperPhase.COMPLETE)
        self.app.queue_whisper_snapshot(card, request_id, snap)
        self.app.queue_card_progress_mode(card, request_id, "reset")
        self.app.queue_whisper_status(card, request_id, "Done", "complete")

        self.app.main_thread_id = threading.get_ident()
        self.app.load_more_button = mock.Mock()
        self.app.load_more_button.winfo_manager.return_value = ""
        self.app.clear_results()
        self._drain_whisper_queue()

        self.assertEqual(self.app.whisper_runs_by_card, {})
        self.assertEqual(self.app.whisper_request_id_by_card, {})
        self.assertIsNone(self.app.latest_whisper_card)

    def test_queue_payload_shapes_for_phase_2c2(self) -> None:
        card = self._register_card()
        request_id = "req-shape"
        self.app.whisper_request_id_by_card[card] = request_id
        snap = _snapshot(request_id)

        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_whisper_snapshot(card, request_id, snap)
        self.app.queue_card_progress_mode(card, request_id, "indeterminate")
        self.app.queue_whisper_status(card, request_id, "Loading model", "downloading")

        action_snap, payload_snap = self.app.ui_queue.get_nowait()
        action_mode, payload_mode = self.app.ui_queue.get_nowait()
        action_status, payload_status = self.app.ui_queue.get_nowait()

        self.assertEqual(action_snap, "whisper_snapshot")
        self.assertEqual(payload_snap, (card, request_id, snap))
        self.assertEqual(action_mode, "card_progress_mode")
        self.assertEqual(payload_mode, (card, request_id, "indeterminate"))
        self.assertEqual(action_status, "whisper_status")
        self.assertEqual(
            payload_status,
            (card, request_id, "Loading model", "downloading"),
        )


class TestPhase2C2WhisperWorker(_AppHarness):
    """End-to-end reuse-only Whisper action with synchronous fake service/resolver."""

    def setUp(self) -> None:
        super().setUp()
        self.captured_requests: list[TranscriptionRequest] = []
        self.transcribe_calls: list[tuple[TranscriptionRequest, Path]] = []
        self.phase_log: list[WhisperPhase] = []
        self.resolve_calls: list[TranscriptionRequest] = []
        self.downloader_calls: list = []

        self.text_path = self.out_dir / "Demo.whisper.en.txt"
        self.srt_path = self.out_dir / "Demo.whisper.en.srt"
        self.text_path.write_text("hello\n", encoding="utf-8")
        self.srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")

        self.fake_service = mock.Mock(spec=WhisperService)
        self.fake_service.list_available_models.return_value = ["tiny", "base", "small"]

        def fake_transcribe(request, source_path, on_phase=None):
            self.transcribe_calls.append((request, Path(source_path)))
            for phase in (
                WhisperPhase.WAITING_FOR_SERVICE,
                WhisperPhase.LOADING_MODEL,
                WhisperPhase.TRANSCRIBING,
                WhisperPhase.WRITING,
                WhisperPhase.COMPLETE,
            ):
                if on_phase is not None:
                    on_phase(phase)
                    self.phase_log.append(phase)
            return TranscriptionResult(
                text_path=self.text_path,
                srt_path=self.srt_path,
                language="en",
                segment_count=1,
                model_name=request.model_name,
                effective_device="cuda",
                fallback_used=False,
            )

        self.fake_service.transcribe.side_effect = fake_transcribe
        self.app.whisper_service = self.fake_service
        self.app.whisper_models = ["tiny", "base", "small"]

        # Real reuse-only resolver; download_manager must never be called in 2C2.
        self.app.download_manager.download = mock.Mock(
            side_effect=lambda *a, **k: self.downloader_calls.append((a, k)) or True
        )
        self.app.download_manager.download_transcript = mock.Mock(
            side_effect=lambda *a, **k: self.downloader_calls.append(("transcript", a, k))
        )
        self.app.audio_source_resolver = AudioSourceResolver(
            download_manager=self.app.download_manager
        )

    def _run_inline_threads(self):
        """Patch Thread so Whisper workers run synchronously on the caller."""

        def capture_thread(target=None, args=(), kwargs=None, daemon=None):
            kwargs = kwargs or {}

            def start():
                if target is not None:
                    target(*args, **kwargs)

            return mock.Mock(start=start)

        return mock.patch("threading.Thread", side_effect=capture_thread)

    def _associate_media(self, card: _FakeCard, media: VerifiedMedia) -> None:
        epoch = self.app.invalidate_verified_media(card)
        self.app.queue_verified_media(card, media, epoch)

    def _copy_diagnostics_text(self) -> str:
        with mock.patch(
            "link2vid.ui.main_window.get_yt_dlp_version",
            return_value="1.2.3",
        ):
            self.app.copy_diagnostics()
        self.assertIsNotNone(self.app.clipboard_text)
        return self.app.clipboard_text

    def test_selected_alias_freezes_one_uuid_request(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "freeze-req.mp4")
        self._associate_media(card, media)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(len(self.transcribe_calls), 1)
        request, _path = self.transcribe_calls[0]
        self.assertIsInstance(request, TranscriptionRequest)
        self.assertEqual(request.model_name, "small")
        uuid.UUID(request.request_id)  # raises if not UUID4-shaped
        self.assertEqual(self.app.whisper_request_id_by_card[card], request.request_id)
        self.assertIs(self.app.latest_whisper_card, card)

    def test_freezes_exact_card_entry_output_and_media(self) -> None:
        card = self._register_card()
        self.app.card_entries[card] = {
            "title": "Exact Title",
            "webpage_url": "https://example.com/exact",
        }
        media = _write_media(self.out_dir / "exact.mp4", b"exact-bytes")
        self._associate_media(card, media)
        other = self._register_card(_FakeCard("other"))
        other_media = _write_media(self.out_dir / "other.mp4", b"other")
        self._associate_media(other, other_media)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "base")
        self._drain_whisper_queue()

        request, source_path = self.transcribe_calls[0]
        self.assertEqual(request.title, "Exact Title")
        self.assertEqual(request.source_url, "https://example.com/exact")
        self.assertEqual(request.output_dir, Path(self.app.output_path))
        self.assertEqual(request.model_name, "base")
        self.assertIs(request.verified_media, media)
        self.assertEqual(source_path, media.path)
        self.assertNotEqual(request.verified_media, other_media)

    def test_valid_media_zero_downloader_provider_calls_exact_path(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "reuse.mp4", b"reuse-me")
        self._associate_media(card, media)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(self.downloader_calls, [])
        self.app.download_manager.download.assert_not_called()
        self.app.download_manager.download_transcript.assert_not_called()
        request, source_path = self.transcribe_calls[0]
        self.assertEqual(source_path, media.path)
        self.assertTrue(media.path.exists())
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.COMPLETE)
        self.assertEqual(snap.source_kind, SourceKind.REUSED.value)

    def test_absent_media_attempts_acquisition_before_service(self) -> None:
        card = self._register_card()
        # No verified_media association; Phase 3A acquires bestaudio via the manager.
        def clobber_reason(stage, err):
            self.app.last_error_reason = "unknown"
            self.app.log_history.append(f"ERROR [{stage}] {err}")

        self.app.log_error = clobber_reason

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.fake_service.transcribe.assert_not_called()
        self.app.download_manager.download.assert_called()
        args, kwargs = self.app.download_manager.download.call_args
        self.assertEqual(args[1], "bestaudio")
        self.app.download_manager.download_transcript.assert_not_called()
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertIn("usable audio", (snap.error_reason or "").lower())
        self.assertEqual(card.state, "failed")
        self.assertIn("usable audio", card.status.lower())

    def test_stale_media_attempts_acquisition_before_service(self) -> None:
        card = self._register_card()
        path = self.out_dir / "stale-media.mp4"
        media = _write_media(path, b"original")
        self._associate_media(card, media)
        path.write_bytes(b"changed-after-verify")  # size/mtime diverge

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.fake_service.transcribe.assert_not_called()
        self.app.download_manager.download.assert_called()
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertIn("usable audio", (snap.error_reason or "").lower())

    def test_busy_rejects_overlap(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "busy.mp4")
        self._associate_media(card, media)
        self.app.busy_cards.add(card)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")

        self.fake_service.transcribe.assert_not_called()
        self.assertNotIn(card, self.app.whisper_request_id_by_card)
        self.assertTrue(any("already has an active action" in msg for msg in self.app.log_history))

    def _assert_start_failure_cleanup(self, card: _FakeCard) -> None:
        self.assertNotIn(card, self.app.busy_cards)
        self.assertTrue(card.actions_enabled)
        self.assertEqual(card.progress_mode, "determinate")
        self.assertEqual(card.progress, 0)
        self.assertEqual(card.state, "failed")
        snapshot = self.app.whisper_runs_by_card[card]
        self.assertEqual(snapshot.phase, WhisperPhase.FAILED)
        self.assertEqual(
            self.app.whisper_request_id_by_card[card],
            snapshot.request_id,
        )
        self.assertIsNotNone(snapshot.error_reason)
        self.fake_service.transcribe.assert_not_called()

    def test_queue_helper_failure_after_busy_rolls_back_start(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "queue-start-fail.mp4")
        self._associate_media(card, media)
        self.app.log_error = mock.Mock(side_effect=RuntimeError("log also failed"))

        with mock.patch.object(
            self.app,
            "queue_whisper_status",
            side_effect=RuntimeError("status queue failed"),
        ), mock.patch("threading.Thread") as thread_cls:
            self.app.handle_card_whisper(card, "small")

        thread_cls.assert_not_called()
        self._assert_start_failure_cleanup(card)
        self.assertEqual(self.app.last_error_reason, "status queue failed")
        self.assertIn("status queue failed", card.status)

    def test_thread_construction_failure_rolls_back_start(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "thread-construct-fail.mp4")
        self._associate_media(card, media)

        with mock.patch(
            "threading.Thread",
            side_effect=RuntimeError("thread construction failed"),
        ):
            self.app.handle_card_whisper(card, "small")

        self._assert_start_failure_cleanup(card)
        self.assertEqual(self.app.last_error_reason, "thread construction failed")

    def test_thread_start_failure_rolls_back_start(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "thread-start-fail.mp4")
        self._associate_media(card, media)
        worker = mock.Mock()
        worker.start.side_effect = RuntimeError("thread start failed")

        with mock.patch("threading.Thread", return_value=worker):
            self.app.handle_card_whisper(card, "small")

        worker.start.assert_called_once_with()
        self._assert_start_failure_cleanup(card)
        self.assertEqual(self.app.last_error_reason, "thread start failed")

    def test_thread_start_failure_remains_copy_diagnostics_authority(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "thread-start-copy-fail.mp4")
        self._associate_media(card, media)
        worker = mock.Mock()
        worker.start.side_effect = RuntimeError(
            "thread start failed token=STARTSECRET\r\nTraceback: hidden"
        )

        with mock.patch("threading.Thread", return_value=worker):
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        snapshot = self.app.whisper_runs_by_card[card]
        self.assertEqual(snapshot.phase, WhisperPhase.FAILED)
        self.assertEqual(
            self.app.whisper_request_id_by_card[card],
            snapshot.request_id,
        )
        with mock.patch(
            "link2vid.ui.main_window.get_yt_dlp_version",
            return_value="1.2.3",
        ):
            self.app.copy_diagnostics()
        text = self.app.clipboard_text
        self.assertIn("Whisper phase: failed", text)
        self.assertIn(
            "Whisper error reason: thread start failed token=[REDACTED]",
            text,
        )
        self.assertNotIn("STARTSECRET", text)
        self.assertNotIn("Traceback", text)

    def test_later_run_replaces_retained_failed_start_request(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "thread-start-replaced.mp4")
        self._associate_media(card, media)
        failed_worker = mock.Mock()
        failed_worker.start.side_effect = RuntimeError("thread start failed")

        with mock.patch("threading.Thread", return_value=failed_worker):
            self.app.handle_card_whisper(card, "small")
        failed_request_id = self.app.whisper_request_id_by_card[card]

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        current_request_id = self.app.whisper_request_id_by_card[card]
        self.assertNotEqual(current_request_id, failed_request_id)
        self.assertEqual(
            self.app.whisper_runs_by_card[card].request_id,
            current_request_id,
        )
        self.assertEqual(
            self.app.whisper_runs_by_card[card].phase,
            WhisperPhase.COMPLETE,
        )

    def test_stale_start_cleanup_is_noop_for_newer_owner(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "newer-owner.mp4")
        old_request = TranscriptionRequest(
            request_id="req-stale-start",
            source_url="https://example.com/stale",
            title="Stale",
            output_dir=self.out_dir,
            model_name="small",
            verified_media=media,
        )
        newer_request_id = "req-newer-start"
        newer_snapshot = _snapshot(
            newer_request_id,
            phase=WhisperPhase.TRANSCRIBING,
        )
        self.app.whisper_request_id_by_card[card] = newer_request_id
        self.app.whisper_runs_by_card[card] = newer_snapshot
        self.app.latest_whisper_card = card
        self.app.busy_cards.add(card)
        card.set_actions_enabled(False)
        card.set_progress_mode("indeterminate")
        card.set_status("Newer request running", state="downloading")
        self.app.last_error_reason = "newer reason"
        logs_before = list(self.app.log_history)

        self.app._cleanup_failed_whisper_start(
            card,
            old_request,
            RuntimeError("stale cleanup"),
        )

        self.assertEqual(
            self.app.whisper_request_id_by_card[card],
            newer_request_id,
        )
        self.assertIs(self.app.whisper_runs_by_card[card], newer_snapshot)
        self.assertIs(self.app.latest_whisper_card, card)
        self.assertIn(card, self.app.busy_cards)
        self.assertFalse(card.actions_enabled)
        self.assertEqual(card.progress_mode, "indeterminate")
        self.assertEqual(card.status, "Newer request running")
        self.assertEqual(card.state, "downloading")
        self.assertEqual(self.app.last_error_reason, "newer reason")
        self.assertEqual(self.app.log_history, logs_before)

    def test_worker_cache_root_failure_emits_failed_and_clears_busy(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "cache-root-fail.mp4")
        request_id = "req-cache-root-fail"
        request = TranscriptionRequest(
            request_id=request_id,
            source_url="https://example.com/cache",
            title="Cache failure",
            output_dir=self.out_dir,
            model_name="small",
            verified_media=media,
        )
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.busy_cards.add(card)
        card.set_actions_enabled(False)
        statuses = []
        snapshots = []
        original_status = self.app.queue_whisper_status
        original_snapshot = self.app.queue_whisper_snapshot

        def track_status(c, rid, status, state=None):
            statuses.append((status, state))
            return original_status(c, rid, status, state)

        def track_snapshot(c, rid, snapshot):
            snapshots.append(snapshot)
            return original_snapshot(c, rid, snapshot)

        self.app.queue_whisper_status = track_status
        self.app.queue_whisper_snapshot = track_snapshot

        with mock.patch(
            "link2vid.ui.main_window.model_cache_dir",
            side_effect=RuntimeError("cache root unavailable"),
        ):
            self.app.run_whisper_transcription(card, request)

        failed_statuses = [
            status for status, state in statuses if state == "failed"
        ]
        failed_snapshots = [
            snapshot
            for snapshot in snapshots
            if snapshot.phase is WhisperPhase.FAILED
        ]
        self.assertEqual(
            failed_statuses,
            ["Whisper failed: cache root unavailable"],
        )
        self.assertEqual(len(failed_snapshots), 1)
        self.assertEqual(
            failed_snapshots[0].error_reason,
            "cache root unavailable",
        )
        self.assertEqual(failed_snapshots[0].cache_root, "")
        self.assertNotIn(card, self.app.busy_cards)
        self.fake_service.transcribe.assert_not_called()

    def test_queue_only_widget_and_map_changes(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "queue-only.mp4")
        self._associate_media(card, media)

        # Force worker-thread enqueue path for the whole run.
        self.app.main_thread_id = threading.get_ident() + 1
        # handle_card_whisper must run on main thread; temporarily restore.
        main_id = threading.get_ident()
        self.app.main_thread_id = main_id

        worker_ran = []

        def run_worker_as_off_main(target=None, args=(), kwargs=None, daemon=None):
            kwargs = kwargs or {}

            def start():
                worker_ran.append(1)
                prev = self.app.main_thread_id
                self.app.main_thread_id = threading.get_ident() + 99
                try:
                    if target is not None:
                        target(*args, **kwargs)
                finally:
                    self.app.main_thread_id = prev

            return mock.Mock(start=start)

        with mock.patch("threading.Thread", side_effect=run_worker_as_off_main):
            self.app.handle_card_whisper(card, "small")

        # After handler (main) + worker (off-main), map may have initial snapshot
        # from main-thread queue helpers, but worker terminal must still be queued.
        self.assertTrue(worker_ran)
        queued_actions = []
        while True:
            try:
                action, _payload = self.app.ui_queue.get_nowait()
            except queue.Empty:
                break
            queued_actions.append(action)
        self.assertIn("whisper_snapshot", queued_actions)
        self.assertIn("whisper_busy", queued_actions)
        self.assertIn("card_progress_mode", queued_actions)

        # Re-run with drain to confirm consumer is the map mutator for terminal.
        card2 = self._register_card(_FakeCard("queue-map"))
        media2 = _write_media(self.out_dir / "queue-map.mp4")
        self._associate_media(card2, media2)
        with mock.patch("threading.Thread", side_effect=run_worker_as_off_main):
            self.app.handle_card_whisper(card2, "tiny")
        # Drain applies worker events; runs map ends COMPLETE.
        self._drain_whisper_queue()
        self.assertEqual(
            self.app.whisper_runs_by_card[card2].phase,
            WhisperPhase.COMPLETE,
        )

    def test_phase_progress_sequence_indeterminate(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "phases.mp4")
        self._associate_media(card, media)
        modes: list[str] = []
        statuses: list[str] = []
        original_mode = self.app.queue_card_progress_mode
        original_status = self.app.queue_whisper_status

        def track_mode(c, rid, mode):
            modes.append(mode)
            return original_mode(c, rid, mode)

        def track_status(c, rid, status, state=None):
            statuses.append(status)
            return original_status(c, rid, status, state)

        self.app.queue_card_progress_mode = track_mode
        self.app.queue_whisper_status = track_status

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertIn("indeterminate", modes)
        self.assertEqual(modes[-1], "reset")
        self.assertTrue(any("Preparing audio" in s for s in statuses))
        self.assertTrue(any("Loading model" in s for s in statuses))
        self.assertTrue(any("Transcribing" in s for s in statuses))
        self.assertTrue(any("Whisper complete:" in s for s in statuses))
        # No fake percentage strings in Whisper statuses.
        self.assertFalse(any("%" in s for s in statuses))

    def test_service_callback_complete_emits_one_canonical_terminal(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "one-complete.mp4")
        self._associate_media(card, media)
        statuses = []
        snapshots = []
        original_status = self.app.queue_whisper_status
        original_snapshot = self.app.queue_whisper_snapshot

        def track_status(c, rid, status, state=None):
            statuses.append((status, state))
            return original_status(c, rid, status, state)

        def track_snapshot(c, rid, snapshot):
            snapshots.append(snapshot)
            return original_snapshot(c, rid, snapshot)

        self.app.queue_whisper_status = track_status
        self.app.queue_whisper_snapshot = track_snapshot

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")

        complete_statuses = [
            status for status, state in statuses if state == "complete"
        ]
        complete_snapshots = [
            snapshot
            for snapshot in snapshots
            if snapshot.phase is WhisperPhase.COMPLETE
        ]
        self.assertEqual(len(complete_statuses), 1)
        self.assertEqual(len(complete_snapshots), 1)
        self.assertIn(self.text_path.name, complete_statuses[0])
        self.assertIn(self.srt_path.name, complete_statuses[0])

    def test_service_callback_failed_emits_one_canonical_terminal(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "one-failed.mp4")
        self._associate_media(card, media)
        statuses = []
        snapshots = []
        original_status = self.app.queue_whisper_status
        original_snapshot = self.app.queue_whisper_snapshot

        def callback_then_fail(request, source_path, on_phase=None):
            if on_phase:
                on_phase(WhisperPhase.FAILED)
            raise RuntimeError("canonical service failure")

        def track_status(c, rid, status, state=None):
            statuses.append((status, state))
            return original_status(c, rid, status, state)

        def track_snapshot(c, rid, snapshot):
            snapshots.append(snapshot)
            return original_snapshot(c, rid, snapshot)

        self.fake_service.transcribe.side_effect = callback_then_fail
        self.app.queue_whisper_status = track_status
        self.app.queue_whisper_snapshot = track_snapshot

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")

        failed_statuses = [
            status for status, state in statuses if state == "failed"
        ]
        failed_snapshots = [
            snapshot
            for snapshot in snapshots
            if snapshot.phase is WhisperPhase.FAILED
        ]
        self.assertEqual(failed_statuses, [
            "Whisper failed: canonical service failure"
        ])
        self.assertEqual(len(failed_snapshots), 1)

    def test_post_result_failures_emit_only_complete_terminal(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "post-result-failures.mp4")
        request_id = "req-post-result-failures"
        request = TranscriptionRequest(
            request_id=request_id,
            source_url="https://example.com/post-result",
            title="Post result",
            output_dir=self.out_dir,
            model_name="small",
            verified_media=media,
        )
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.busy_cards.add(card)
        card.set_actions_enabled(False)
        original_status = self.app.queue_whisper_status
        original_snapshot = self.app.queue_whisper_snapshot

        def result_then_break_nonterminal_helpers(
            service_request,
            source_path,
            on_phase=None,
        ):
            self.app.log = mock.Mock(side_effect=RuntimeError("completion log failed"))
            self.app.set_progress = mock.Mock(
                side_effect=RuntimeError("completion progress failed")
            )
            return TranscriptionResult(
                text_path=self.text_path,
                srt_path=self.srt_path,
                language="en",
                segment_count=1,
                model_name=service_request.model_name,
                effective_device="cuda",
                fallback_used=False,
            )

        def fail_complete_status(c, rid, status, state=None):
            if state == "complete":
                raise RuntimeError("complete status helper failed")
            return original_status(c, rid, status, state)

        def fail_complete_snapshot(c, rid, snapshot):
            if snapshot.phase is WhisperPhase.COMPLETE:
                raise RuntimeError("complete snapshot helper failed")
            return original_snapshot(c, rid, snapshot)

        self.fake_service.transcribe.side_effect = (
            result_then_break_nonterminal_helpers
        )
        self.app.queue_whisper_status = fail_complete_status
        self.app.queue_whisper_snapshot = fail_complete_snapshot
        self.app.main_thread_id = threading.get_ident() + 1

        self.app.run_whisper_transcription(card, request)

        queued = []
        while True:
            try:
                queued.append(self.app.ui_queue.get_nowait())
            except queue.Empty:
                break
        terminal_snapshots = [
            payload[2]
            for action, payload in queued
            if action == "whisper_snapshot"
            and payload[2].phase in (WhisperPhase.COMPLETE, WhisperPhase.FAILED)
        ]
        terminal_statuses = [
            payload
            for action, payload in queued
            if action == "whisper_status"
            and payload[3] in ("complete", "failed")
        ]
        self.assertEqual(
            [snapshot.phase for snapshot in terminal_snapshots],
            [WhisperPhase.COMPLETE],
        )
        self.assertEqual(
            [payload[3] for payload in terminal_statuses],
            ["complete"],
        )
        self.assertFalse(
            any(
                snapshot.phase is WhisperPhase.FAILED
                for snapshot in terminal_snapshots
            )
        )

        for item in queued:
            self.app.ui_queue.put(item)
        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()
        self.assertEqual(
            self.app.whisper_runs_by_card[card].phase,
            WhisperPhase.COMPLETE,
        )
        self.assertEqual(card.state, "complete")
        self.assertNotIn(card, self.app.busy_cards)

    def test_failed_status_helper_failure_still_yields_snapshot(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "failed-status-helper.mp4")
        self._associate_media(card, media)
        original_status = self.app.queue_whisper_status

        def service_failure(request, source_path, on_phase=None):
            raise RuntimeError("service failed for status test")

        def fail_failed_status(c, rid, status, state=None):
            if state == "failed":
                raise RuntimeError("failed status helper failed")
            return original_status(c, rid, status, state)

        self.fake_service.transcribe.side_effect = service_failure
        self.app.queue_whisper_status = fail_failed_status
        self.app.main_thread_id = threading.get_ident() + 1

        request_id = "req-failed-status-helper"
        request = TranscriptionRequest(
            request_id=request_id,
            source_url="https://example.com/status-helper",
            title="Status helper",
            output_dir=self.out_dir,
            model_name="small",
            verified_media=media,
        )
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.busy_cards.add(card)
        self.app.run_whisper_transcription(card, request)

        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()
        self.assertEqual(
            self.app.whisper_runs_by_card[card].phase,
            WhisperPhase.FAILED,
        )
        self.assertEqual(card.state, "failed")

    def test_failed_snapshot_helper_failure_still_yields_status(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "failed-snapshot-helper.mp4")
        self._associate_media(card, media)
        original_snapshot = self.app.queue_whisper_snapshot

        def service_failure(request, source_path, on_phase=None):
            raise RuntimeError("service failed for snapshot test")

        def fail_failed_snapshot(c, rid, snapshot):
            if snapshot.phase is WhisperPhase.FAILED:
                raise RuntimeError("failed snapshot helper failed")
            return original_snapshot(c, rid, snapshot)

        self.fake_service.transcribe.side_effect = service_failure
        self.app.queue_whisper_snapshot = fail_failed_snapshot
        self.app.main_thread_id = threading.get_ident() + 1

        request_id = "req-failed-snapshot-helper"
        request = TranscriptionRequest(
            request_id=request_id,
            source_url="https://example.com/snapshot-helper",
            title="Snapshot helper",
            output_dir=self.out_dir,
            model_name="small",
            verified_media=media,
        )
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.busy_cards.add(card)
        self.app.run_whisper_transcription(card, request)

        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()
        self.assertEqual(card.state, "failed")
        self.assertIn("service failed for snapshot test", card.status)
        self.assertEqual(
            self.app.whisper_runs_by_card[card].phase,
            WhisperPhase.FAILED,
        )

    def test_reset_helper_failure_still_clears_busy(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "reset-helper-fail.mp4")
        self._associate_media(card, media)
        original_progress_mode = self.app.queue_card_progress_mode

        def fail_reset(c, rid, mode):
            if mode == "reset":
                raise RuntimeError("reset helper failed")
            return original_progress_mode(c, rid, mode)

        self.app.queue_card_progress_mode = fail_reset
        self.app.main_thread_id = threading.get_ident() + 1
        request_id = "req-reset-helper"
        request = TranscriptionRequest(
            request_id=request_id,
            source_url="https://example.com/reset-helper",
            title="Reset helper",
            output_dir=self.out_dir,
            model_name="small",
            verified_media=media,
        )
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.busy_cards.add(card)
        card.set_actions_enabled(False)

        self.app.run_whisper_transcription(card, request)

        self.app.main_thread_id = threading.get_ident()
        self._drain_whisper_queue()
        self.assertNotIn(card, self.app.busy_cards)
        self.assertTrue(card.actions_enabled)
        self.assertEqual(card.progress_mode, "determinate")
        self.assertEqual(card.progress, 0)

    def test_success_result_and_snapshot_name_both_outputs(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "success.mp4")
        self._associate_media(card, media)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.COMPLETE)
        self.assertEqual(snap.effective_device, "cuda")
        self.assertFalse(snap.fallback_used)
        self.assertIsNone(snap.error_reason)
        self.assertIn(self.text_path.name, card.status)
        self.assertIn(self.srt_path.name, card.status)
        self.assertEqual(card.state, "complete")
        self.assertTrue(
            any(self.text_path.name in msg and self.srt_path.name in msg for msg in self.app.log_history)
        )
        self.assertNotIn(card, self.app.busy_cards)
        self.assertEqual(card.progress_mode, "determinate")

    def test_service_failure_sanitized_snapshot(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "svc-fail.mp4")
        self._associate_media(card, media)

        def boom(request, source_path, on_phase=None):
            if on_phase:
                on_phase(WhisperPhase.LOADING_MODEL)
            raise RuntimeError("native decode exploded\ntraceback line")

        self.fake_service.transcribe.side_effect = boom
        def clobber_reason(stage, err):
            self.app.last_error_reason = "unknown"
            self.app.log_history.append(f"ERROR [{stage}] {err}")

        self.app.log_error = clobber_reason

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertEqual(snap.error_reason, "native decode exploded")
        self.assertIn("Whisper failed:", card.status)
        self.assertNotIn("traceback line", card.status)
        self.assertTrue(any("native decode exploded" in msg for msg in self.app.log_history))
        self.assertNotIn(card, self.app.busy_cards)
        self.assertEqual(self.app.last_error_reason, "native decode exploded")

    def test_source_failure_before_service(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "src-fail.mp4")
        self._associate_media(card, media)
        self.app.audio_source_resolver = mock.Mock(spec=AudioSourceResolver)
        self.app.audio_source_resolver.resolve.side_effect = AudioAcquisitionError(
            "Audio download failed; no usable audio source was produced."
        )

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.fake_service.transcribe.assert_not_called()
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertIn("usable audio", (snap.error_reason or "").lower())

    def test_generic_failure_copy_diagnostics_redacts_unsafe_details(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "generic-diagnostics-fail.mp4")
        self._associate_media(card, media)
        self.fake_service.transcribe.side_effect = RuntimeError(
            "GPU init failed\x00 cookie=COOKIESECRET "
            "Authorization: Bearer AUTHSECRET "
            "at C:\\Users\\alice\\AppData\\Local\\Temp\\model.bin "
            "from https://example.test/model?token=URLSECRET"
            "\r\nTraceback (most recent call last): password=TRACESECRET"
        )

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()
        text = self._copy_diagnostics_text()

        snapshot = self.app.whisper_runs_by_card[card]
        self.assertEqual(snapshot.phase, WhisperPhase.FAILED)
        self.assertIn("cookie=[REDACTED]", snapshot.error_reason)
        self.assertIn("Authorization=[REDACTED]", snapshot.error_reason)
        self.assertIn("[REDACTED PATH]", snapshot.error_reason)
        self.assertIn(
            f"Whisper cache root: {snapshot.cache_root}",
            text,
        )
        for secret in (
            "COOKIESECRET",
            "AUTHSECRET",
            "URLSECRET",
            "TRACESECRET",
            "alice",
            "Traceback",
        ):
            self.assertNotIn(secret, text)
        reason_lines = [
            line for line in text.splitlines() if line.startswith("Whisper error reason:")
        ]
        self.assertEqual(len(reason_lines), 1)
        self.assertLessEqual(
            len(reason_lines[0].removeprefix("Whisper error reason: ")),
            240,
        )
        self.assertNotRegex(text, r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def test_real_copy_redacts_complete_cookie_and_set_cookie_headers(self) -> None:
        reasons = (
            "Request denied Cookie: session=SESSIONSECRET; "
            "csrftoken=CSRFSECRET; theme=dark",
            "Request denied Set-Cookie: access_token=ACCESSSECRET; "
            "csrftoken=SECONDSECRET; Path=/; HttpOnly",
        )
        self.fake_service.transcribe.side_effect = [
            RuntimeError(reason) for reason in reasons
        ]

        for index, header in enumerate(("Cookie", "Set-Cookie"), start=1):
            card = self._register_card(_FakeCard(f"cookie-header-{index}"))
            media = _write_media(
                self.out_dir / f"cookie-header-{index}.mp4",
                f"media-{index}".encode(),
            )
            self._associate_media(card, media)
            with self._run_inline_threads():
                self.app.handle_card_whisper(card, "small")
            self._drain_whisper_queue()
            text = self._copy_diagnostics_text()

            self.assertIn(
                f"Whisper error reason: Request denied {header}: [REDACTED]",
                text,
            )
            for leaked in (
                "SESSIONSECRET",
                "CSRFSECRET",
                "ACCESSSECRET",
                "SECONDSECRET",
                "csrftoken",
                "theme=dark",
                "HttpOnly",
            ):
                self.assertNotIn(leaked, text)

    def test_real_copy_redacts_embedded_credential_keys(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "embedded-credentials.mp4")
        self._associate_media(card, media)
        self.fake_service.transcribe.side_effect = RuntimeError(
            "Request failed csrftoken=CSRFSECRET access_token=ACCESSSECRET "
            "authToken=AUTHSECRET client_secret=CLIENTSECRET safe=value"
        )

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()
        text = self._copy_diagnostics_text()

        self.assertIn("csrftoken=[REDACTED]", text)
        self.assertIn("access_token=[REDACTED]", text)
        self.assertIn("authToken=[REDACTED]", text)
        self.assertIn("client_secret=[REDACTED]", text)
        self.assertIn("safe=value", text)
        for secret in (
            "CSRFSECRET",
            "ACCESSSECRET",
            "AUTHSECRET",
            "CLIENTSECRET",
        ):
            self.assertNotIn(secret, text)

    def test_real_copy_redacts_unquoted_paths_with_spaces_through_line_end(self) -> None:
        cases = (
            (
                "windows",
                "Model failed at C:\\Users\\alice\\AppData\\Local\\Temp\\"
                "Whisper Jobs\\secret model.bin windows suffix leak",
                ("alice", "secret model.bin", "windows suffix leak"),
            ),
            (
                "unc",
                "Model failed at \\\\server\\share\\Whisper Jobs\\"
                "secret model.bin unc suffix leak",
                ("server", "secret model.bin", "unc suffix leak"),
            ),
            (
                "posix-user",
                "Model failed at /Users/alice/Whisper Jobs/"
                "secret model.bin posix suffix leak",
                ("alice", "secret model.bin", "posix suffix leak"),
            ),
            (
                "posix-temp",
                "Model failed at /tmp/Whisper Jobs/"
                "secret model.bin temp suffix leak",
                ("secret model.bin", "temp suffix leak"),
            ),
        )
        self.fake_service.transcribe.side_effect = [
            RuntimeError(reason) for _name, reason, _leaks in cases
        ]

        for index, (name, _reason, leaks) in enumerate(cases, start=1):
            card = self._register_card(_FakeCard(f"path-{name}"))
            media = _write_media(
                self.out_dir / f"path-{index}.mp4",
                f"path-media-{index}".encode(),
            )
            self._associate_media(card, media)
            with self._run_inline_threads():
                self.app.handle_card_whisper(card, "small")
            self._drain_whisper_queue()
            text = self._copy_diagnostics_text()

            self.assertIn(
                "Whisper error reason: Model failed at [REDACTED PATH]",
                text,
            )
            for leak in leaks:
                self.assertNotIn(leak, text)

    def test_real_copy_replaces_first_line_traceback_banner(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "traceback-banner.mp4")
        self._associate_media(card, media)
        self.fake_service.transcribe.side_effect = RuntimeError(
            "Traceback (most recent call last):"
            "\n  File \"C:\\Users\\alice\\worker.py\", line 42, in run"
            "\nRuntimeError: token=TRACESECRET"
        )

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()
        text = self._copy_diagnostics_text()

        self.assertEqual(
            self.app.whisper_runs_by_card[card].error_reason,
            "RuntimeError: transcription failed",
        )
        self.assertIn(
            "Whisper error reason: RuntimeError: transcription failed",
            text,
        )
        self.assertNotIn("Traceback", text)
        self.assertNotIn("worker.py", text)
        self.assertNotIn("TRACESECRET", text)

    def test_audio_acquisition_failure_copy_diagnostics_redacts_details(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "audio-diagnostics-fail.mp4")
        self._associate_media(card, media)
        self.app.audio_source_resolver = mock.Mock(spec=AudioSourceResolver)
        self.app.audio_source_resolver.resolve.side_effect = AudioAcquisitionError(
            "Audio download failed\x00 token=AUDIOSECRET password='PASSSECRET' "
            "at /tmp/link2vid/private.wav and /Users/bob/Library/cache.bin "
            "from https://audio.test/get?authorization=QUERYSECRET"
            + (" detail" * 80)
            + "\nTraceback: cookie=TRACECOOKIE"
        )

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()
        text = self._copy_diagnostics_text()

        self.fake_service.transcribe.assert_not_called()
        snapshot = self.app.whisper_runs_by_card[card]
        self.assertEqual(snapshot.phase, WhisperPhase.FAILED)
        self.assertIn("token=[REDACTED]", snapshot.error_reason)
        self.assertIn("password=[REDACTED]", snapshot.error_reason)
        self.assertIn("[REDACTED PATH]", snapshot.error_reason)
        self.assertIn(
            f"Whisper cache root: {snapshot.cache_root}",
            text,
        )
        for secret in (
            "AUDIOSECRET",
            "PASSSECRET",
            "QUERYSECRET",
            "TRACECOOKIE",
            "/tmp/link2vid",
            "/Users/bob",
            "Traceback",
        ):
            self.assertNotIn(secret, text)
        reason_lines = [
            line for line in text.splitlines() if line.startswith("Whisper error reason:")
        ]
        self.assertEqual(len(reason_lines), 1)
        self.assertLessEqual(
            len(reason_lines[0].removeprefix("Whisper error reason: ")),
            240,
        )

    def test_audio_acquisition_error_uses_hard_length_limit(self) -> None:
        reason = self.app._sanitize_whisper_error(
            AudioAcquisitionError(
                "Audio download failed access_token=AUDIOSECRET"
                + (" detail" * 80)
                + "\nTraceback: hidden"
            )
        )
        self.assertIn("access_token=[REDACTED]", reason)
        self.assertNotIn("AUDIOSECRET", reason)
        self.assertNotIn("Traceback", reason)
        self.assertEqual(len(reason), 240)
        self.assertTrue(reason.endswith("..."))

    def test_cookie_error_uses_same_bounded_redaction_boundary(self) -> None:
        error = CookiesRequiredError(
            original=RuntimeError(
                "Cookie retry failed cookie=COOKIESECRET"
                + (" detail" * 80)
                + "\nTraceback: hidden"
            )
        )
        reason = self.app._sanitize_whisper_error(error)
        self.assertIn("cookie=[REDACTED]", reason)
        self.assertNotIn("COOKIESECRET", reason)
        self.assertNotIn("Traceback", reason)
        self.assertEqual(len(reason), 240)
        self.assertTrue(reason.endswith("..."))

    def test_lease_cleanup_reused_media_survives(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "survive.mp4", b"keep-me")
        self._associate_media(card, media)
        releases = []

        real_resolve = self.app.audio_source_resolver.resolve

        def resolve_and_track(request, progress_hook=None):
            lease = real_resolve(request, progress_hook=progress_hook)
            original_release = lease.release

            def tracked_release():
                releases.append(lease.path)
                return original_release()

            lease.release = tracked_release
            return lease

        self.app.audio_source_resolver.resolve = resolve_and_track

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertTrue(media.path.exists())
        self.assertEqual(media.path.read_bytes(), b"keep-me")
        self.assertTrue(releases)  # release called (idempotent no-op for reused)

    def test_gpu_fallback_visibility_propagates(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "fallback.mp4")
        self._associate_media(card, media)

        def fallback_transcribe(request, source_path, on_phase=None):
            for phase in (
                WhisperPhase.WAITING_FOR_SERVICE,
                WhisperPhase.LOADING_MODEL,
                WhisperPhase.FALLING_BACK_CPU,
                WhisperPhase.TRANSCRIBING,
                WhisperPhase.WRITING,
                WhisperPhase.COMPLETE,
            ):
                if on_phase:
                    on_phase(phase)
            return TranscriptionResult(
                text_path=self.text_path,
                srt_path=self.srt_path,
                language="en",
                segment_count=1,
                model_name=request.model_name,
                effective_device="cpu",
                fallback_used=True,
            )

        self.fake_service.transcribe.side_effect = fallback_transcribe

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        snap = self.app.whisper_runs_by_card[card]
        self.assertTrue(snap.fallback_used)
        self.assertEqual(snap.effective_device, "cpu")
        self.assertIn("CPU fallback", card.status)
        self.assertTrue(any("falling back to CPU" in msg for msg in self.app.log_history))

    def test_request_mismatch_destroyed_clear_during_run(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "clear-during.mp4")
        self._associate_media(card, media)

        def clear_mid_transcribe(request, source_path, on_phase=None):
            # Simulate clear_results while worker is in service.
            self.app.load_more_button = mock.Mock()
            self.app.load_more_button.winfo_manager.return_value = ""
            self.app.clear_results()
            if on_phase:
                on_phase(WhisperPhase.TRANSCRIBING)
            return TranscriptionResult(
                text_path=self.text_path,
                srt_path=self.srt_path,
                language="en",
                segment_count=1,
                model_name=request.model_name,
                effective_device="cuda",
                fallback_used=False,
            )

        self.fake_service.transcribe.side_effect = clear_mid_transcribe

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(self.app.whisper_runs_by_card, {})
        self.assertEqual(self.app.whisper_request_id_by_card, {})
        self.assertFalse(card.winfo_exists())

    def test_stale_terminal_cannot_enable_newer_action(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "stale-term.mp4")
        self._associate_media(card, media)

        gate = threading.Event()
        released = threading.Event()

        def blocking_transcribe(request, source_path, on_phase=None):
            gate.set()
            released.wait(timeout=2)
            if on_phase:
                on_phase(WhisperPhase.TRANSCRIBING)
            return TranscriptionResult(
                text_path=self.text_path,
                srt_path=self.srt_path,
                language="en",
                segment_count=1,
                model_name=request.model_name,
                effective_device="cuda",
                fallback_used=False,
            )

        self.fake_service.transcribe.side_effect = blocking_transcribe

        # Freeze request on the main thread, then run the worker on a real thread
        # without patching threading.Thread (avoids mock recursion).
        first_id = str(uuid.uuid4())
        request = TranscriptionRequest(
            request_id=first_id,
            source_url="https://example.com/v",
            title="Demo",
            output_dir=Path(self.app.output_path),
            model_name="small",
            verified_media=media,
        )
        self.app.whisper_request_id_by_card[card] = first_id
        self.app.latest_whisper_card = card
        self.app.busy_cards.add(card)
        card.set_actions_enabled(False)

        worker = threading.Thread(
            target=self.app.run_whisper_transcription,
            args=(card, request),
            daemon=True,
        )
        worker.start()
        self.assertTrue(gate.wait(timeout=2))

        # Newer action owns the card: bump request id and keep busy.
        newer_id = "req-newer-owner"
        self.app.whisper_request_id_by_card[card] = newer_id
        self.app.busy_cards.add(card)
        card.set_actions_enabled(False)

        # Stale worker finishes and queues terminal busy=False for first_id.
        released.set()
        worker.join(timeout=2)
        self._drain_whisper_queue()

        # Stale whisper_busy clear must not remove busy for the newer request.
        self.assertIn(card, self.app.busy_cards)
        self.assertEqual(self.app.whisper_request_id_by_card[card], newer_id)
        self.app.queue_whisper_busy(card, first_id, False)
        self.assertIn(card, self.app.busy_cards)

    def test_dependency_empty_controls_still_wire_callback(self) -> None:
        """Empty aliases disable Whisper on the card; Download/Transcript stay wired."""
        self.app.whisper_models = []
        created = []

        def fake_video_card(parent, **kwargs):
            card = _FakeCard(kwargs.get("title", "card"))
            card.kwargs = kwargs
            # Mimic pack / thumbnail APIs used by render_next_batch.
            card.pack = lambda **kw: None
            card.set_thumbnail_image = lambda image: None
            created.append(card)
            return card

        self.app.video_entries = [
            {"title": "Demo", "webpage_url": "https://example.com/v"}
        ]
        self.app.rendered_count = 0
        self.app.batch_size = 20
        self.app.results_scroll = mock.Mock()
        self.app.load_more_button = mock.Mock()
        self.app.load_more_button.winfo_manager.return_value = ""
        self.app.get_transcript_options = lambda entry: None
        self.app.get_source_label = lambda entry: "default"
        self.app.get_placeholder_image = lambda label: mock.Mock()
        self.app.make_ctk_image = lambda image: image
        self.app.queue_thumbnail = lambda *a, **k: None
        self.app.build_metadata = lambda entry: ""
        self.app.update_card_buttons = lambda has_folder: None
        self.app.format_options = [{"format": "best", "label": "Best"}]
        self.app.handle_card_download = mock.Mock()
        self.app.handle_card_transcript = mock.Mock()

        with mock.patch("link2vid.ui.main_window.VideoCard", new=fake_video_card):
            self.app.render_next_batch = VideoDownloaderApp.render_next_batch.__get__(
                self.app
            )
            self.app.render_next_batch()

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].kwargs.get("whisper_models"), [])
        self.assertIs(
            created[0].kwargs.get("on_whisper"),
            self.app.handle_card_whisper,
        )
        self.assertIs(
            created[0].kwargs.get("on_transcript"),
            self.app.handle_card_transcript,
        )
        self.assertIs(
            created[0].kwargs.get("on_download"),
            self.app.handle_card_download,
        )

    def test_provider_transcript_callback_unchanged(self) -> None:
        card = self._register_card()
        result = mock.Mock(source="manual", languages=["en"])
        self.app.download_manager.download_transcript.return_value = result
        self.app.download_manager.download_transcript.side_effect = None

        # handle_card_transcript still starts download_transcript worker.
        with self._run_inline_threads():
            self.app.handle_card_transcript(card)

        self.app.download_manager.download_transcript.assert_called_once()
        self.assertEqual(card.state, "complete")
        self.assertIn("Transcript saved", card.status)
        # Whisper maps untouched.
        self.assertEqual(self.app.whisper_runs_by_card, {})

    def test_rejects_alias_not_in_probed_list(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "bad-alias.mp4")
        self._associate_media(card, media)
        self.app.whisper_models = ["tiny", "small"]

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "large-v3")

        self.fake_service.transcribe.assert_not_called()
        self.assertNotIn(card, self.app.whisper_request_id_by_card)

    def test_whisper_busy_queue_payload_shape(self) -> None:
        card = self._register_card()
        request_id = "req-busy-shape"
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_whisper_busy(card, request_id, False)
        action, payload = self.app.ui_queue.get_nowait()
        self.assertEqual(action, "whisper_busy")
        self.assertEqual(payload, (card, request_id, False))


class TestPhase3BAcquisitionAndCookies(_AppHarness):
    """Transcript-only acquisition progress, cleanup, and one cookies.txt retry."""

    def setUp(self) -> None:
        super().setUp()
        self.transcribe_calls: list[tuple[TranscriptionRequest, Path]] = []
        self.text_path = self.out_dir / "Demo.whisper.en.txt"
        self.srt_path = self.out_dir / "Demo.whisper.en.srt"
        self.text_path.write_text("hello\n", encoding="utf-8")
        self.srt_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            encoding="utf-8",
        )

        self.fake_service = mock.Mock(spec=WhisperService)
        self.fake_service.list_available_models.return_value = ["tiny", "base", "small"]

        def fake_transcribe(request, source_path, on_phase=None):
            self.transcribe_calls.append((request, Path(source_path)))
            for phase in (
                WhisperPhase.WAITING_FOR_SERVICE,
                WhisperPhase.LOADING_MODEL,
                WhisperPhase.TRANSCRIBING,
                WhisperPhase.WRITING,
                WhisperPhase.COMPLETE,
            ):
                if on_phase is not None:
                    on_phase(phase)
            return TranscriptionResult(
                text_path=self.text_path,
                srt_path=self.srt_path,
                language="en",
                segment_count=1,
                model_name=request.model_name,
                effective_device="cuda",
                fallback_used=False,
            )

        self.fake_service.transcribe.side_effect = fake_transcribe
        self.app.whisper_service = self.fake_service
        self.app.whisper_models = ["tiny", "base", "small"]
        self.app.audio_source_resolver = AudioSourceResolver(
            download_manager=self.app.download_manager
        )

    def _run_inline_threads(self):
        def capture_thread(target=None, args=(), kwargs=None, daemon=None):
            kwargs = kwargs or {}

            def start():
                if target is not None:
                    target(*args, **kwargs)

            return mock.Mock(start=start)

        return mock.patch("threading.Thread", side_effect=capture_thread)

    def _associate_media(self, card: _FakeCard, media: VerifiedMedia) -> None:
        epoch = self.app.invalidate_verified_media(card)
        self.app.queue_verified_media(card, media, epoch)

    def _install_temp_download(
        self,
        *,
        progress_events: list[dict] | None = None,
        side_effect=None,
        workspaces: list[Path] | None = None,
    ):
        """Install a download_manager.download that writes one audio file."""

        def fake_download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            if side_effect is not None:
                result = side_effect(url, format_id, outtmpl, progress_hook, post_hook)
                if result is not None:
                    return result
            outtmpl_path = Path(outtmpl)
            workspace = outtmpl_path.parent
            if workspaces is not None:
                workspaces.append(workspace)
            audio_path = workspace / "temp-audio.m4a"
            audio_path.write_bytes(b"temp-audio-bytes")
            if progress_hook and progress_events:
                for event in progress_events:
                    progress_hook(event)
            elif progress_hook:
                progress_hook(
                    {
                        "status": "downloading",
                        "downloaded_bytes": 50,
                        "total_bytes": 100,
                    }
                )
                progress_hook({"status": "finished", "filename": str(audio_path)})
            if post_hook:
                post_hook(str(audio_path))
            return True

        self.app.download_manager.download = mock.Mock(side_effect=fake_download)
        return fake_download

    def _start_direct_worker(
        self,
        card: _FakeCard,
        request_id: str,
        *,
        source_url: str = "https://example.com/threaded-cookie",
        name: str | None = None,
    ) -> threading.Thread:
        request = TranscriptionRequest(
            request_id=request_id,
            source_url=source_url,
            title="Threaded cookie",
            output_dir=self.out_dir,
            model_name="small",
            verified_media=None,
        )
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.busy_cards.add(card)
        card.set_actions_enabled(False)
        worker = threading.Thread(
            target=self.app.run_whisper_transcription,
            args=(card, request),
            daemon=True,
            name=name,
        )
        worker.start()
        return worker

    def _pump_ui_until_worker_stops(
        self,
        worker: threading.Thread,
        *,
        timeout: float = 2.0,
    ) -> None:
        """Drain production queue consumers with a finite deadlock timeout."""
        self.app.root.after = mock.Mock()
        deadline = time.monotonic() + timeout
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.process_ui_queue()
            time.sleep(0.005)
        worker.join(timeout=max(0.0, deadline - time.monotonic()))
        self.app.process_ui_queue()
        self.assertFalse(worker.is_alive(), "Whisper worker deadlocked on UI queue")

    def test_reused_path_no_download_or_progress_flash(self) -> None:
        card = self._register_card()
        media = _write_media(self.out_dir / "reuse-no-flash.mp4", b"reuse-me")
        self._associate_media(card, media)
        progress_values: list[float] = []
        modes: list[str] = []
        original_progress = self.app.queue_whisper_card_progress
        original_mode = self.app.queue_card_progress_mode

        def track_progress(c, rid, value):
            progress_values.append(value)
            return original_progress(c, rid, value)

        def track_mode(c, rid, mode):
            modes.append(mode)
            return original_mode(c, rid, mode)

        self.app.queue_whisper_card_progress = track_progress
        self.app.queue_card_progress_mode = track_mode
        self.app.download_manager.download = mock.Mock(
            side_effect=AssertionError("reuse must not download")
        )

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.app.download_manager.download.assert_not_called()
        self.assertEqual(progress_values, [])
        self.assertNotIn("determinate", modes)
        self.assertEqual(
            self.app.whisper_runs_by_card[card].source_kind,
            SourceKind.REUSED.value,
        )
        self.assertTrue(media.path.exists())

    def test_no_media_temp_acquisition_success_and_cleanup(self) -> None:
        card = self._register_card()
        workspaces: list[Path] = []
        self._install_temp_download(workspaces=workspaces)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(len(self.transcribe_calls), 1)
        _request, source_path = self.transcribe_calls[0]
        self.assertEqual(source_path.name, "temp-audio.m4a")
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.COMPLETE)
        self.assertEqual(snap.source_kind, SourceKind.TEMPORARY.value)
        self.assertTrue(self.text_path.exists())
        self.assertTrue(self.srt_path.exists())
        self.assertEqual(len(workspaces), 1)
        self.assertFalse(workspaces[0].exists())
        self.assertFalse(source_path.exists())

    def test_whisper_handler_and_worker_leave_active_footer_untouched(self) -> None:
        card = self._register_card()
        self._install_temp_download()
        self.app.set_progress(0.63)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.app.progress.set.assert_called_once_with(0.63)
        self.app.progress_bar.set.assert_called_once_with(0.63)
        self.assertEqual(
            self.app.whisper_runs_by_card[card].phase,
            WhisperPhase.COMPLETE,
        )

    def test_determinate_real_progress_then_indeterminate_model(self) -> None:
        card = self._register_card()
        modes: list[str] = []
        statuses: list[str] = []
        progress_values: list[float] = []
        original_mode = self.app.queue_card_progress_mode
        original_status = self.app.queue_whisper_status
        original_progress = self.app.queue_whisper_card_progress

        def track_mode(c, rid, mode):
            modes.append(mode)
            return original_mode(c, rid, mode)

        def track_status(c, rid, status, state=None):
            statuses.append(status)
            return original_status(c, rid, status, state)

        def track_progress(c, rid, value):
            progress_values.append(value)
            return original_progress(c, rid, value)

        self.app.queue_card_progress_mode = track_mode
        self.app.queue_whisper_status = track_status
        self.app.queue_whisper_card_progress = track_progress
        self._install_temp_download(
            progress_events=[
                {
                    "status": "downloading",
                    "downloaded_bytes": 25,
                    "total_bytes": 100,
                },
                {
                    "status": "downloading",
                    "downloaded_bytes": 75,
                    "total_bytes": 100,
                },
            ]
        )

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertIn("Preparing audio", statuses)
        self.assertIn("Downloading audio", statuses)
        self.assertIn("Loading model", statuses)
        self.assertIn("Transcribing", statuses)
        download_idx = statuses.index("Downloading audio")
        loading_idx = statuses.index("Loading model")
        self.assertLess(download_idx, loading_idx)
        self.assertEqual(progress_values, [0.25, 0.75])
        self.assertIn("determinate", modes)
        # Model phases return to indeterminate after acquisition.
        determinate_idx = modes.index("determinate")
        self.assertIn("indeterminate", modes[determinate_idx + 1 :])
        self.assertEqual(modes[-1], "reset")
        self.assertFalse(any("%" in status for status in statuses))

    def test_adversarial_progress_is_real_clamped_and_monotonic(self) -> None:
        card = self._register_card()
        modes: list[str] = []
        statuses: list[str] = []
        progress_values: list[float] = []
        invalid_events_stayed_preparing: list[bool] = []
        global_progress_unchanged: list[bool] = []
        original_mode = self.app.queue_card_progress_mode
        original_status = self.app.queue_whisper_status
        original_progress = self.app.queue_whisper_card_progress

        def track_mode(c, rid, mode):
            modes.append(mode)
            return original_mode(c, rid, mode)

        def track_status(c, rid, status, state=None):
            statuses.append(status)
            return original_status(c, rid, status, state)

        def track_progress(c, rid, value):
            progress_values.append(value)
            return original_progress(c, rid, value)

        self.app.queue_card_progress_mode = track_mode
        self.app.queue_whisper_status = track_status
        self.app.queue_whisper_card_progress = track_progress

        def fake_download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            workspace = Path(outtmpl).parent
            audio_path = workspace / "adversarial.m4a"
            audio_path.write_bytes(b"audio")
            global_calls_before = self.app.progress.set.call_count
            for invalid in (
                None,
                "not-a-dict",
                {},
                {"status": "downloading"},
                {"status": "downloading", "downloaded_bytes": 0},
                {
                    "status": "downloading",
                    "downloaded_bytes": 5,
                    "total_bytes": 0,
                },
                {"status": "finished"},
                {"status": "finished", "filename": str(workspace / "missing.m4a")},
            ):
                progress_hook(invalid)
            invalid_events_stayed_preparing.append(
                "determinate" not in modes
                and "Downloading audio" not in statuses
                and card.progress is None
            )
            # Estimate-only is supported and is the first truthful value.
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 40,
                    "total_bytes_estimate": 100,
                }
            )
            # A refined estimate moving backward is ignored by policy.
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 30,
                    "total_bytes_estimate": 100,
                }
            )
            # Real values over 100% are clamped to 1.0.
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 120,
                    "total_bytes": 100,
                }
            )
            # Lower and duplicate finished values cannot move/refresh the bar.
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 80,
                    "total_bytes": 100,
                }
            )
            progress_hook({"status": "finished", "filename": str(audio_path)})
            global_progress_unchanged.append(
                self.app.progress.set.call_count == global_calls_before
            )
            if post_hook:
                post_hook(str(audio_path))
            return True

        self.app.download_manager.download = mock.Mock(side_effect=fake_download)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(invalid_events_stayed_preparing, [True])
        self.assertEqual(progress_values, [0.4, 1.0])
        self.assertEqual(global_progress_unchanged, [True])
        self.assertEqual(statuses.count("Downloading audio"), 1)
        self.assertEqual(modes.count("determinate"), 1)
        self.assertIn("indeterminate", modes[modes.index("determinate") + 1 :])
        self.assertEqual(
            self.app.whisper_runs_by_card[card].phase,
            WhisperPhase.COMPLETE,
        )

    def test_finished_paths_without_prior_fraction_are_ignored(self) -> None:
        card = self._register_card()
        modes: list[str] = []
        progress_values: list[float] = []
        original_mode = self.app.queue_card_progress_mode
        original_progress = self.app.queue_whisper_card_progress

        def track_mode(c, rid, mode):
            modes.append(mode)
            return original_mode(c, rid, mode)

        def track_progress(c, rid, value):
            progress_values.append(value)
            return original_progress(c, rid, value)

        self.app.queue_card_progress_mode = track_mode
        self.app.queue_whisper_card_progress = track_progress
        outside_path = self.out_dir / "outside.m4a"
        preexisting_path = self.out_dir / "preexisting.m4a"
        preexisting_path.write_bytes(b"preexisting")
        ignored_while_preparing: list[bool] = []

        def fake_download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            workspace = Path(outtmpl).parent
            audio_path = workspace / "finished-only.m4a"
            audio_path.write_bytes(b"audio")
            for path in (outside_path, preexisting_path, audio_path):
                progress_hook({"status": "finished", "filename": str(path)})
            ignored_while_preparing.append(
                "determinate" not in modes
                and progress_values == []
                and card.progress is None
            )
            if post_hook:
                post_hook(str(audio_path))
            return True

        self.app.download_manager.download = mock.Mock(side_effect=fake_download)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(ignored_while_preparing, [True])
        self.assertEqual(progress_values, [])
        self.assertNotIn("determinate", modes)
        self.assertEqual(
            self.app.whisper_runs_by_card[card].phase,
            WhisperPhase.COMPLETE,
        )

    def test_valid_prior_fraction_then_finished_reports_one(self) -> None:
        card = self._register_card()
        progress_values: list[float] = []
        original_progress = self.app.queue_whisper_card_progress

        def track_progress(c, rid, value):
            progress_values.append(value)
            return original_progress(c, rid, value)

        self.app.queue_whisper_card_progress = track_progress

        def fake_download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            workspace = Path(outtmpl).parent
            audio_path = workspace / "fraction-finished.m4a"
            audio_path.write_bytes(b"audio")
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 25,
                    "total_bytes": 100,
                }
            )
            # No filepath is consulted: prior real bytes ground completion.
            progress_hook({"status": "finished"})
            if post_hook:
                post_hook(str(audio_path))
            return True

        self.app.download_manager.download = mock.Mock(side_effect=fake_download)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(progress_values, [0.25, 1.0])
        self.assertEqual(
            self.app.whisper_runs_by_card[card].phase,
            WhisperPhase.COMPLETE,
        )

    def test_cookie_first_workspace_cleaned_before_prompt(self) -> None:
        card = self._register_card()
        workspaces: list[Path] = []
        prompt_saw_clean = []

        def side_effect(url, format_id, outtmpl, progress_hook, post_hook):
            workspace = Path(outtmpl).parent
            workspaces.append(workspace)
            (workspace / "partial.m4a.part").write_bytes(b"partial")
            raise CookiesRequiredError(original=RuntimeError("need cookies"))

        self._install_temp_download(side_effect=side_effect)

        def choose_and_observe():
            prompt_saw_clean.append(all(not ws.exists() for ws in workspaces))
            return None

        self.app.ui_choose_cookies_browser = mock.Mock(side_effect=choose_and_observe)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.app.ui_choose_cookies_browser.assert_called_once()
        self.assertEqual(prompt_saw_clean, [True])
        self.assertTrue(all(not ws.exists() for ws in workspaces))
        self.fake_service.transcribe.assert_not_called()
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertIn("declined", (snap.error_reason or "").lower())

    def test_real_download_manager_applies_selected_cookie_on_retry(self) -> None:
        card = self._register_card()
        ydl_options: list[dict] = []
        download_attempts = {"count": 0}

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts
                ydl_options.append(dict(opts))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, urls):
                download_attempts["count"] += 1
                if download_attempts["count"] == 1:
                    raise RuntimeError("cookies required")
                outtmpl = self.opts["outtmpl"]
                audio_path = Path(
                    outtmpl.replace("%(title)s", "retry-audio").replace(
                        "%(ext)s", "m4a"
                    )
                )
                audio_path.write_bytes(b"retry-audio")
                for hook in self.opts["progress_hooks"]:
                    hook(
                        {
                            "status": "downloading",
                            "downloaded_bytes": 10,
                            "total_bytes": 10,
                        }
                    )
                    hook({"status": "finished", "filename": str(audio_path)})
                for hook in self.opts["post_hooks"]:
                    hook(str(audio_path))

        manager = DownloadManager(
            ydl_logger=mock.Mock(),
            log=lambda message: self.app.log_history.append(message),
            log_error=self.app.log_error,
            get_cookies_path=lambda: self.app.cookies_path,
            get_cookies_browser=lambda: self.app.cookies_browser,
        )
        manager._browser_candidates = lambda: []
        self.app.download_manager = manager
        self.app.audio_source_resolver = AudioSourceResolver(
            download_manager=manager
        )

        def choose_browser():
            self.assertTrue(self.app._whisper_cookie_retry_lock.locked())
            return "brave"

        self.app.ui_choose_cookies_browser = mock.Mock(side_effect=choose_browser)

        with mock.patch(
            "link2vid.core.downloader.yt_dlp.YoutubeDL",
            new=FakeYoutubeDL,
        ), self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(download_attempts["count"], 2)
        self.assertEqual(len(ydl_options), 2)
        self.assertNotIn("cookiesfrombrowser", ydl_options[0])
        self.assertEqual(ydl_options[1]["format"], "bestaudio")
        self.assertEqual(ydl_options[1]["cookiesfrombrowser"], ("brave",))
        self.assertEqual(manager.last_cookies_mode, "browser")
        self.app.ui_choose_cookies_browser.assert_called_once()
        self.assertEqual(self.app.cookies_browser, "brave")
        self.assertEqual(len(self.transcribe_calls), 1)
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.COMPLETE)
        self.assertEqual(snap.source_kind, SourceKind.TEMPORARY.value)
        workspaces = [Path(opts["outtmpl"]).parent for opts in ydl_options]
        self.assertEqual(len(set(workspaces)), 2)
        self.assertTrue(all(not workspace.exists() for workspace in workspaces))
        self.assertTrue(self.text_path.exists())
        self.assertTrue(self.srt_path.exists())

    def test_threaded_cookie_prompt_and_selection_queue_without_deadlock(self) -> None:
        card = self._register_card()
        workspaces: list[Path] = []
        attempts = {"count": 0}
        ui_order: list[str] = []
        retry_saw_lock: list[bool] = []

        def download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            attempts["count"] += 1
            workspace = Path(outtmpl).parent
            workspaces.append(workspace)
            if attempts["count"] == 1:
                raise CookiesRequiredError(original=RuntimeError("auth"))
            retry_saw_lock.append(self.app._whisper_cookie_retry_lock.locked())
            audio_path = workspace / "threaded-retry.m4a"
            audio_path.write_bytes(b"audio")
            if post_hook:
                post_hook(str(audio_path))
            return True

        self.app.download_manager.download = mock.Mock(side_effect=download)
        self.app.audio_source_resolver = AudioSourceResolver(
            download_manager=self.app.download_manager
        )
        self.app.ui_choose_cookies_browser = VideoDownloaderApp.ui_choose_cookies_browser.__get__(
            self.app
        )

        def choose_browser(parent, suggested=None):
            self.assertTrue(self.app._whisper_cookie_retry_lock.locked())
            ui_order.append("choose")
            return "brave"

        with mock.patch(
            "link2vid.ui.main_window.prompt_cookie_browser",
            side_effect=choose_browser,
        ):
            worker = self._start_direct_worker(card, "req-threaded-cookie")
            # Before the main test pumps "call", the worker must be waiting.
            deadline = time.monotonic() + 1.0
            while self.app.ui_queue.empty() and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(worker.is_alive())
            self._pump_ui_until_worker_stops(worker)

        self.assertEqual(ui_order, ["choose"])
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(retry_saw_lock, [True])
        self.assertEqual(len(self.transcribe_calls), 1)
        self.assertEqual(
            self.app.whisper_runs_by_card[card].phase,
            WhisperPhase.COMPLETE,
        )
        self.assertNotIn(card, self.app.busy_cards)
        self.assertEqual(len(workspaces), 2)
        self.assertTrue(all(not workspace.exists() for workspace in workspaces))

    def test_cookie_retry_lock_prevents_cross_applied_paths(self) -> None:
        card_a = self._register_card(_FakeCard("cookie-a"))
        card_b = self._register_card(_FakeCard("cookie-b"))
        cookie_a = self.out_dir / "cookies-a.txt"
        cookie_b = self.out_dir / "cookies-b.txt"
        cookie_a.write_text("a", encoding="utf-8")
        cookie_b.write_text("b", encoding="utf-8")
        attempts: dict[str, int] = {}
        applied_paths: dict[str, str | None] = {}
        confirmations: list[str] = []
        workspaces: list[Path] = []
        a_retry_entered = threading.Event()
        release_a_retry = threading.Event()
        b_first_failed = threading.Event()

        def download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            key = url.rsplit("/", 1)[-1]
            attempts[key] = attempts.get(key, 0) + 1
            workspace = Path(outtmpl).parent
            workspaces.append(workspace)
            if attempts[key] == 1:
                if key == "b":
                    b_first_failed.set()
                raise CookiesRequiredError(original=RuntimeError(f"auth-{key}"))

            self.assertTrue(self.app._whisper_cookie_retry_lock.locked())
            applied_paths[key] = self.app.cookies_browser
            if key == "a":
                a_retry_entered.set()
                self.assertTrue(
                    release_a_retry.wait(timeout=1.5),
                    "timed out holding first cookie retry",
                )
            audio_path = workspace / f"{key}.m4a"
            audio_path.write_bytes(key.encode("utf-8"))
            if post_hook:
                post_hook(str(audio_path))
            return True

        def choose():
            name = threading.current_thread().name
            confirmations.append(name)
            return "brave" if name == "cookie-a" else "chrome"

        self.app.download_manager.download = mock.Mock(side_effect=download)
        self.app.audio_source_resolver = AudioSourceResolver(
            download_manager=self.app.download_manager
        )
        self.app.ui_choose_cookies_browser = mock.Mock(side_effect=choose)

        worker_a = self._start_direct_worker(
            card_a,
            "req-cookie-a",
            source_url="https://example.com/a",
            name="cookie-a",
        )
        self.assertTrue(a_retry_entered.wait(timeout=1.0))
        worker_b = self._start_direct_worker(
            card_b,
            "req-cookie-b",
            source_url="https://example.com/b",
            name="cookie-b",
        )
        self.assertTrue(b_first_failed.wait(timeout=1.0))
        time.sleep(0.05)
        self.assertEqual(confirmations, ["cookie-a"])
        self.assertTrue(worker_b.is_alive())

        release_a_retry.set()
        worker_a.join(timeout=2.0)
        worker_b.join(timeout=2.0)
        self.assertFalse(worker_a.is_alive())
        self.assertFalse(worker_b.is_alive())
        self._drain_whisper_queue()

        self.assertEqual(confirmations, ["cookie-a"])
        self.assertEqual(applied_paths["a"], "brave")
        self.assertEqual(applied_paths["b"], "brave")
        self.assertEqual(attempts, {"a": 2, "b": 2})
        self.assertEqual(
            self.app.whisper_runs_by_card[card_a].phase,
            WhisperPhase.COMPLETE,
        )
        self.assertEqual(
            self.app.whisper_runs_by_card[card_b].phase,
            WhisperPhase.COMPLETE,
        )
        self.assertNotIn(card_a, self.app.busy_cards)
        self.assertNotIn(card_b, self.app.busy_cards)
        self.assertTrue(all(not workspace.exists() for workspace in workspaces))

    def test_threaded_cookie_prompt_exception_unblocks_worker(self) -> None:
        card = self._register_card()
        workspaces: list[Path] = []

        def download(url, format_id, outtmpl, progress_hook=None, post_hook=None, **_kwargs):
            workspaces.append(Path(outtmpl).parent)
            raise CookiesRequiredError(original=RuntimeError("auth"))

        self.app.download_manager.download = mock.Mock(side_effect=download)
        self.app.audio_source_resolver = AudioSourceResolver(
            download_manager=self.app.download_manager
        )
        self.app.ui_choose_cookies_browser = VideoDownloaderApp.ui_choose_cookies_browser.__get__(
            self.app
        )

        with mock.patch(
            "link2vid.ui.main_window.prompt_cookie_browser",
            side_effect=RuntimeError("window closing"),
        ):
            worker = self._start_direct_worker(card, "req-threaded-shutdown")
            self._pump_ui_until_worker_stops(worker)
        self.assertEqual(self.app.download_manager.download.call_count, 1)
        self.fake_service.transcribe.assert_not_called()
        snapshot = self.app.whisper_runs_by_card[card]
        self.assertEqual(snapshot.phase, WhisperPhase.FAILED)
        self.assertIn("prompt failed", (snapshot.error_reason or "").lower())
        self.assertNotIn(card, self.app.busy_cards)
        self.assertTrue(all(not workspace.exists() for workspace in workspaces))

    def test_cookie_selected_file_one_retry_succeeds(self) -> None:
        card = self._register_card()
        workspaces: list[Path] = []
        attempts = {"count": 0}
        def side_effect(url, format_id, outtmpl, progress_hook, post_hook):
            attempts["count"] += 1
            workspace = Path(outtmpl).parent
            workspaces.append(workspace)
            if attempts["count"] == 1:
                (workspace / "blocked.part").write_bytes(b"x")
                raise CookiesRequiredError(original=RuntimeError("auth"))
            audio_path = workspace / "retry-audio.m4a"
            audio_path.write_bytes(b"retry-audio")
            if progress_hook:
                progress_hook(
                    {
                        "status": "downloading",
                        "downloaded_bytes": 10,
                        "total_bytes": 10,
                    }
                )
                progress_hook({"status": "finished", "filename": str(audio_path)})
            if post_hook:
                post_hook(str(audio_path))
            return True

        self.app.download_manager.download = mock.Mock(side_effect=side_effect)
        self.app.ui_choose_cookies_browser = mock.Mock(return_value="brave")

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(attempts["count"], 2)
        self.app.ui_choose_cookies_browser.assert_called_once()
        self.assertEqual(self.app.cookies_browser, "brave")
        self.assertEqual(len(self.transcribe_calls), 1)
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.COMPLETE)
        self.assertEqual(snap.source_kind, SourceKind.TEMPORARY.value)
        self.assertEqual(len(workspaces), 2)
        self.assertNotEqual(workspaces[0], workspaces[1])
        self.assertTrue(all(not ws.exists() for ws in workspaces))
        self.assertTrue(self.text_path.exists())
        self.assertTrue(self.srt_path.exists())

    def test_cookie_cancel_fails_clearly(self) -> None:
        card = self._register_card()

        def side_effect(url, format_id, outtmpl, progress_hook, post_hook):
            raise CookiesRequiredError(original=RuntimeError("auth"))

        self.app.download_manager.download = mock.Mock(side_effect=side_effect)
        self.app.ui_choose_cookies_browser = mock.Mock(return_value=None)

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.app.ui_choose_cookies_browser.assert_called_once()
        self.fake_service.transcribe.assert_not_called()
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertIn("declined", (snap.error_reason or "").lower())
        self.assertEqual(card.state, "failed")
        self.assertNotIn(card, self.app.busy_cards)

    def test_cookie_no_selection_fails_clearly(self) -> None:
        card = self._register_card()

        def side_effect(url, format_id, outtmpl, progress_hook, post_hook):
            raise CookiesRequiredError(original=RuntimeError("auth"))

        self.app.download_manager.download = mock.Mock(side_effect=side_effect)
        self.app.cookies_browser = "brave"

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.app.ui_choose_cookies_browser.assert_not_called()
        self.fake_service.transcribe.assert_not_called()
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertIn("still required", (snap.error_reason or "").lower())
        self.assertEqual(self.app.download_manager.download.call_count, 2)

    def test_second_cookie_failure_no_prompt_loop(self) -> None:
        card = self._register_card()
        workspaces: list[Path] = []

        def side_effect(url, format_id, outtmpl, progress_hook, post_hook):
            workspace = Path(outtmpl).parent
            workspaces.append(workspace)
            raise CookiesRequiredError(original=RuntimeError("still auth"))

        self.app.download_manager.download = mock.Mock(side_effect=side_effect)
        self.app.ui_choose_cookies_browser = mock.Mock(return_value="brave")

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.assertEqual(self.app.download_manager.download.call_count, 2)
        self.app.ui_choose_cookies_browser.assert_called_once()
        self.fake_service.transcribe.assert_not_called()
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertIn("still required", (snap.error_reason or "").lower())
        self.assertTrue(all(not ws.exists() for ws in workspaces))

    def test_cookie_prompt_failure_fails_clearly(self) -> None:
        card = self._register_card()

        def side_effect(url, format_id, outtmpl, progress_hook, post_hook):
            raise CookiesRequiredError(original=RuntimeError("auth"))

        self.app.download_manager.download = mock.Mock(side_effect=side_effect)
        self.app.ui_choose_cookies_browser = mock.Mock(
            side_effect=RuntimeError("dialog failed")
        )

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        self.fake_service.transcribe.assert_not_called()
        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertIn("prompt failed", (snap.error_reason or "").lower())
        self.assertNotIn(card, self.app.busy_cards)

    def test_service_failure_cleans_temporary_workspace(self) -> None:
        card = self._register_card()
        workspaces: list[Path] = []
        self._install_temp_download(workspaces=workspaces)

        def boom(request, source_path, on_phase=None):
            if on_phase:
                on_phase(WhisperPhase.LOADING_MODEL)
            raise RuntimeError("service exploded")

        self.fake_service.transcribe.side_effect = boom

        with self._run_inline_threads():
            self.app.handle_card_whisper(card, "small")
        self._drain_whisper_queue()

        snap = self.app.whisper_runs_by_card[card]
        self.assertEqual(snap.phase, WhisperPhase.FAILED)
        self.assertEqual(snap.source_kind, SourceKind.TEMPORARY.value)
        self.assertEqual(len(workspaces), 1)
        self.assertFalse(workspaces[0].exists())
        self.assertNotIn(card, self.app.busy_cards)

    def test_stale_request_drops_acquisition_progress(self) -> None:
        card = self._register_card()
        request_id = "req-current"
        stale_id = "req-stale"
        self.app.whisper_request_id_by_card[card] = request_id
        card.progress = 0.1
        card.progress_mode = "determinate"

        self.app.queue_whisper_card_progress(card, stale_id, 0.9)
        self._drain_whisper_queue()

        self.assertEqual(card.progress, 0.1)
        self.app.queue_whisper_card_progress(card, request_id, 0.55)
        self._drain_whisper_queue()
        self.assertEqual(card.progress, 0.55)

    def test_whisper_card_progress_queue_payload_shape(self) -> None:
        card = self._register_card()
        request_id = "req-progress-shape"
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.main_thread_id = threading.get_ident() + 1
        self.app.queue_whisper_card_progress(card, request_id, 0.42)
        action, payload = self.app.ui_queue.get_nowait()
        self.assertEqual(action, "whisper_card_progress")
        self.assertEqual(payload, (card, request_id, 0.42))

    def test_provider_transcript_regression_unchanged(self) -> None:
        card = self._register_card()
        result = mock.Mock(source="manual", languages=["en"])
        self.app.download_manager.download_transcript = mock.Mock(return_value=result)
        self.app.download_manager.download = mock.Mock(
            side_effect=AssertionError("provider transcript must not download media")
        )

        with self._run_inline_threads():
            self.app.handle_card_transcript(card)

        self.app.download_manager.download_transcript.assert_called_once()
        args, kwargs = self.app.download_manager.download_transcript.call_args
        self.assertIn(".transcript.", args[1])
        self.app.download_manager.download.assert_not_called()
        self.assertEqual(card.state, "complete")
        self.assertIn("Transcript saved", card.status)
        self.assertEqual(self.app.whisper_runs_by_card, {})


class TestWhisperDiagnosticsSelection(_AppHarness):
    """Phase 4A: deterministic Copy Diagnostics selection from latest Whisper card."""

    def _store_run(
        self,
        card: _FakeCard,
        request_id: str,
        *,
        phase: WhisperPhase = WhisperPhase.COMPLETE,
        model_name: str = "small",
        source_kind: str | None = "reused",
        effective_device: str | None = "cuda",
        fallback_used: bool = False,
        error_reason: str | None = None,
        cache_root: str = "C:\\models",
    ) -> WhisperRunSnapshot:
        snapshot = WhisperRunSnapshot(
            request_id=request_id,
            phase=phase,
            model_name=model_name,
            cache_root=cache_root,
            effective_device=effective_device,
            fallback_used=fallback_used,
            source_kind=source_kind,
            error_reason=error_reason,
            updated_at=time.time(),
        )
        self.app.whisper_request_id_by_card[card] = request_id
        self.app.whisper_runs_by_card[card] = snapshot
        self.app.latest_whisper_card = card
        return snapshot

    def _copied_text(self) -> str:
        with mock.patch(
            "link2vid.ui.main_window.get_yt_dlp_version",
            return_value="1.2.3",
        ):
            self.app.copy_diagnostics()
        self.assertIsNotNone(self.app.clipboard_text)
        return self.app.clipboard_text

    def test_no_run_omits_whisper_fields(self) -> None:
        self.assertIsNone(self.app._latest_whisper_diagnostics_input())
        text = self._copied_text()
        self.assertNotIn("Whisper model:", text)
        self.assertIn("Link2Vid Diagnostics", text)
        self.assertIn("yt-dlp version: 1.2.3", text)

    def test_preparing_no_source_fields(self) -> None:
        card = self._register_card()
        self._store_run(
            card,
            "req-prep",
            phase=WhisperPhase.PREPARING_AUDIO,
            source_kind=None,
            effective_device=None,
            fallback_used=False,
            error_reason=None,
        )
        payload = self.app._latest_whisper_diagnostics_input()
        self.assertEqual(
            payload,
            WhisperDiagnosticsInput(
                model_name="small",
                phase="preparing_audio",
                cache_root="C:\\models",
                source_kind=None,
                effective_device=None,
                fallback_used=False,
                error_reason=None,
            ),
        )
        text = self._copied_text()
        self.assertIn("Whisper model: small", text)
        self.assertIn("Whisper phase: preparing_audio", text)
        self.assertIn("Whisper source kind: n/a", text)
        self.assertIn("Whisper effective device: n/a", text)
        self.assertIn("Whisper fallback used: no", text)
        self.assertIn("Whisper error reason: n/a", text)

    def test_reused_success_metadata(self) -> None:
        card = self._register_card()
        self._store_run(
            card,
            "req-reused",
            source_kind="reused",
            effective_device="cuda",
            fallback_used=False,
        )
        text = self._copied_text()
        self.assertIn("Whisper source kind: reused", text)
        self.assertIn("Whisper effective device: cuda", text)
        self.assertIn("Whisper fallback used: no", text)
        self.assertIn("Whisper phase: complete", text)

    def test_temporary_success_metadata(self) -> None:
        card = self._register_card()
        self._store_run(
            card,
            "req-temp",
            source_kind="temporary",
            effective_device="cuda",
            fallback_used=False,
        )
        text = self._copied_text()
        self.assertIn("Whisper source kind: temporary", text)
        self.assertIn("Whisper effective device: cuda", text)

    def test_cpu_fallback_metadata(self) -> None:
        card = self._register_card()
        self._store_run(
            card,
            "req-fallback",
            source_kind="reused",
            effective_device="cpu",
            fallback_used=True,
        )
        text = self._copied_text()
        self.assertIn("Whisper effective device: cpu", text)
        self.assertIn("Whisper fallback used: yes", text)

    def test_failed_sanitized_reason(self) -> None:
        card = self._register_card()
        reason = "Audio download failed; no usable audio source was produced"
        self._store_run(
            card,
            "req-failed",
            phase=WhisperPhase.FAILED,
            source_kind="temporary",
            effective_device=None,
            error_reason=reason,
        )
        text = self._copied_text()
        self.assertIn(f"Whisper error reason: {reason}", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("cookie=", text.lower())
        self.assertNotIn("password", text.lower())

    def test_latest_of_two_cards_wins(self) -> None:
        first = self._register_card(_FakeCard("first"))
        second = self._register_card(_FakeCard("second"))
        self._store_run(
            first,
            "req-first",
            model_name="tiny",
            source_kind="reused",
            effective_device="cuda",
        )
        self._store_run(
            second,
            "req-second",
            model_name="base",
            source_kind="temporary",
            effective_device="cpu",
            fallback_used=True,
        )
        self.assertIs(self.app.latest_whisper_card, second)
        text = self._copied_text()
        self.assertIn("Whisper model: base", text)
        self.assertIn("Whisper source kind: temporary", text)
        self.assertIn("Whisper effective device: cpu", text)
        self.assertIn("Whisper fallback used: yes", text)
        self.assertNotIn("Whisper model: tiny", text)

    def test_request_mismatch_does_not_leak_snapshot(self) -> None:
        card = self._register_card()
        self._store_run(card, "req-current", model_name="small")
        # Snapshot from an older run must not be copied.
        self.app.whisper_runs_by_card[card] = WhisperRunSnapshot(
            request_id="req-stale",
            phase=WhisperPhase.COMPLETE,
            model_name="tiny",
            cache_root="C:\\old-models",
            effective_device="cuda",
            fallback_used=False,
            source_kind="reused",
            error_reason=None,
            updated_at=time.time(),
        )
        self.assertIsNone(self.app._latest_whisper_diagnostics_input())
        text = self._copied_text()
        self.assertNotIn("Whisper model:", text)
        self.assertNotIn("tiny", text)
        self.assertNotIn("old-models", text)

    def test_destroyed_card_does_not_leak_snapshot(self) -> None:
        card = self._register_card()
        self._store_run(card, "req-destroyed", model_name="small")
        card.destroy()
        self.assertIsNone(self.app._latest_whisper_diagnostics_input())
        text = self._copied_text()
        self.assertNotIn("Whisper model:", text)

    def test_clear_results_clears_diagnostics_selection(self) -> None:
        card = self._register_card()
        self._store_run(card, "req-clear-diag", model_name="small")
        self.app.load_more_button = mock.Mock()
        self.app.load_more_button.winfo_manager.return_value = ""
        self.app.clear_results()
        self.assertIsNone(self.app.latest_whisper_card)
        self.assertIsNone(self.app._latest_whisper_diagnostics_input())
        text = self._copied_text()
        self.assertNotIn("Whisper model:", text)

    def test_legacy_non_whisper_diagnostics_unchanged(self) -> None:
        self.app.last_action_kind = "transcript"
        self.app.last_selected_title = "Caption Title"
        self.app.last_selected_format = "best"
        self.app.last_transcript_source = "subtitles"
        self.app.last_transcript_languages = ["en"]
        self.app.last_error = "Boom"
        self.app.last_error_reason = "cookies/auth"
        self.app.log_history = ["legacy-line"]
        self.app.download_manager.last_cookies_mode = "cookies.txt"
        self.app.download_manager.last_cookies_browser = "brave"
        self.app.download_manager.last_js_runtime = "node"
        self.app.download_manager.last_js_runtime_path = "C:\\node.exe"
        self.app.download_manager.last_remote_components = ["ejs:github"]
        self.app.ffmpeg_path = "C:\\ffmpeg.exe"
        self.app.js_runtimes = ["node"]
        self.app.url_entry.get.return_value = "https://example.com/video"
        self.app.output_path = "C:\\out"
        self.app.debug_log_path = "C:\\out\\debug.log"

        text = self._copied_text()
        expected = "\n".join(
            build_diagnostics(
                url="https://example.com/video",
                action_kind="transcript",
                selected_title="Caption Title",
                selected_format="best",
                output_path="C:\\out",
                debug_log_path="C:\\out\\debug.log",
                transcript_source="subtitles",
                transcript_languages=["en"],
                yt_dlp_version="1.2.3",
                ffmpeg_path="C:\\ffmpeg.exe",
                js_runtime="node",
                js_runtime_used="node",
                js_runtime_path="C:\\node.exe",
                remote_components=["ejs:github"],
                cookies_mode="cookies.txt",
                cookies_browser="brave",
                last_error="Boom",
                last_error_reason="cookies/auth",
                log_history=["legacy-line"],
                whisper=None,
            )
        )
        self.assertEqual(text, expected)
        self.assertNotIn("Whisper model:", text)


if __name__ == "__main__":
    unittest.main()
