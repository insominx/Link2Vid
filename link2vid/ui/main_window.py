from __future__ import annotations

import customtkinter as ctk
from tkinter import TclError, filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import math
import threading
import queue
import os
import shutil
import json
import time
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..core.dev_defaults import dev_credentials_for_url, dev_domain_for_url, resolve_login_plan
from ..core import (
    AudioAcquisitionError,
    AudioSourceResolver,
    CookiesRequiredError,
    DirectHlsFound,
    DownloadManager,
    NoTranscriptAvailableError,
    FetchError,
    FetchResults,
    NeedsCookies,
    NeedsSelenium,
    SourceKind,
    TranscriptionRequest,
    TranscriptionResult,
    VerifiedMedia,
    VideoFetcher,
    WhisperPhase,
    WhisperRunSnapshot,
    WhisperService,
    WhisperDiagnosticsInput,
    build_diagnostics,
    classify_error,
    get_error_guidance,
    download_with_ffmpeg,
    entry_watch_url,
    get_format_options,
    get_yt_dlp_version,
    normalize_url,
    sanitize_filename,
    sanitize_whisper_error_reason,
    unique_output_path,
    selenium_fetch_media_entries,
    url_from_clipboard_text,
)
from ..core.extractors import build_media_entries, title_from_page_url
from ..core.runtime import model_cache_dir
from .components import BatchBar, FooterBar, LogDrawer, VideoCard, prompt_credentials
from .thumbnail_loader import ThumbnailLoader

# Card status labels for Whisper phases (unknown-duration work stays indeterminate).
_WHISPER_PHASE_STATUS = {
    WhisperPhase.PREPARING_AUDIO: "Preparing audio",
    WhisperPhase.DOWNLOADING_AUDIO: "Downloading audio",
    WhisperPhase.WAITING_FOR_SERVICE: "Waiting for service",
    WhisperPhase.LOADING_MODEL: "Loading model",
    WhisperPhase.FALLING_BACK_CPU: "Falling back to CPU",
    WhisperPhase.TRANSCRIBING: "Transcribing",
    WhisperPhase.WRITING: "Writing transcript",
}
_WHISPER_INDETERMINATE_PHASES = frozenset(
    {
        WhisperPhase.PREPARING_AUDIO,
        WhisperPhase.WAITING_FOR_SERVICE,
        WhisperPhase.LOADING_MODEL,
        WhisperPhase.FALLING_BACK_CPU,
        WhisperPhase.TRANSCRIBING,
        WhisperPhase.WRITING,
    }
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


@dataclass
class BatchPromptPolicy:
    lock: threading.Lock = field(default_factory=threading.Lock)
    cookies_answer: bool | None = None
    format_fallback_answer: bool | None = None
    ffmpeg_answer: str | None = None


@dataclass(frozen=True)
class BatchItem:
    index: int
    title: str
    url: str
    format_id: str | None
    fmt_label: str | None
    transcript_track: object | None


@dataclass
class BatchRun:
    kind: str
    items: tuple[BatchItem, ...]
    generation: int
    folder: str
    cancel: threading.Event = field(default_factory=threading.Event)
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    default_format_count: int = 0


def format_duration_text(duration) -> str | None:
    try:
        total = int(duration)
    except (TypeError, ValueError):
        return None
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def ffmpeg_progress_display(
    fraction: float,
    elapsed: float | None,
    duration: float | None,
) -> tuple[int, str | None, str | None]:
    clamped_fraction = max(0.0, min(1.0, fraction))
    pct = int(clamped_fraction * 100)
    if duration is None or duration <= 0:
        return pct, format_duration_text(elapsed), None
    display_elapsed = elapsed
    if display_elapsed is None:
        display_elapsed = clamped_fraction * duration
    display_elapsed = max(0.0, min(display_elapsed, duration))
    return pct, format_duration_text(display_elapsed), format_duration_text(duration)


class YtDlpLogger:
    def __init__(self, app):
        self.app = app

    def debug(self, msg):
        # Suppress debug noise by default
        return

    def warning(self, msg):
        self.app.log(f"[yt-dlp] {msg}")

    def error(self, msg):
        self.app.log(f"[yt-dlp] {msg}")


class VideoDownloaderApp:
    def __init__(
        self,
        root,
        *,
        whisper_service: WhisperService | None = None,
        audio_source_resolver: AudioSourceResolver | None = None,
    ):
        self.root = root
        self.root.title('Video Downloader (Cards UI)')
        self.root.geometry('1100x800')
        self.root.minsize(900, 700)
        self.video_entries = []
        self.entries_generation = 0
        self.cards = []
        self.card_entries = {}
        self.selected_entry_indexes: set[int] = set()
        self.entry_format_by_index: dict[int, str] = {}
        self.entry_transcript_by_index: dict[int, object | None] = {}
        self.card_index_by_card: dict = {}
        self.card_by_index: dict[int, object] = {}
        self.batch_run: BatchRun | None = None
        self.batch_prompt_policy: BatchPromptPolicy | None = None
        self.format_options = get_format_options()
        self.format_label_map = {opt["format"]: opt["label"] for opt in self.format_options}
        self.format_label_map.setdefault("best", "Best (single file)")
        self.dev_defaults = self.load_dev_defaults()
        self.main_thread_id = threading.get_ident()
        self.ui_queue = queue.Queue()
        self.log_history = []
        self.log_max = 200
        self.debug_log_path = self._default_debug_log_path()
        self.last_error = None
        self.last_error_reason = None
        self.last_action_kind = None
        self.last_selected_format = None
        self.last_selected_label = None
        self.last_selected_title = None
        self.last_transcript_source = None
        self.last_transcript_languages = []
        self.last_url = None
        self.shown_error_guidance = set()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.fetch_future = None
        self.is_fetching = False
        self.batch_size = 20
        self.rendered_count = 0
        self.thumbnail_size = (120, 72)
        self.thumbnail_styles = {
            "site_a": ("Site", ("#1f2937", "#0ea5e9")),
            "site_b": ("Video", ("#312e81", "#22c55e")),
            "site_c": ("Media", ("#4b5563", "#f59e0b")),
            "default": ("Video", ("#4b5563", "#9ca3af")),
        }
        self.placeholder_cache = {}
        self.placeholder_font = None
        self.busy_cards = set()
        # Main-thread/session map: exact verified media for Whisper reuse (Phase 2B).
        # Only process_ui_queue / main-thread helpers mutate this; workers enqueue events.
        self.verified_media_by_card: dict = {}
        self._verified_media_epoch_by_card: dict = {}
        # Whisper session state (Phase 2C1). Maps mutate only via the UI queue consumer.
        self.whisper_runs_by_card: dict = {}
        self.whisper_request_id_by_card: dict = {}
        self.latest_whisper_card = None
        self.whisper_models: list[str] = []
        # Serialize the interactive selection plus immediate retry so two
        # Whisper workers cannot apply each other's app-global cookies path.
        self._whisper_cookie_retry_lock = threading.Lock()
        self.output_path = self.get_default_output_path()
        self.cookies_path = None
        self.ydl_logger = YtDlpLogger(self)
        self.download_manager = DownloadManager(
            ydl_logger=self.ydl_logger,
            log=self.log,
            log_error=self.log_error,
            dev_defaults=self.dev_defaults,
            get_cookies_path=lambda: self.cookies_path,
        )
        # Per-app ownership: no module singleton. Construction stays lazy (no native import).
        self.whisper_service = whisper_service if whisper_service is not None else WhisperService()
        self.audio_source_resolver = (
            audio_source_resolver
            if audio_source_resolver is not None
            else AudioSourceResolver(download_manager=self.download_manager)
        )
        self.fetcher = VideoFetcher(
            get_video_info=self.download_manager.get_video_info,
            log=self.log,
            dev_defaults=self.dev_defaults,
        )
        self.thumbnail_loader = ThumbnailLoader(self.executor, log=self.log)

        font_big = ("Arial", 22)
        font_med = ("Arial", 16)
        font_small = ("Consolas", 12)

        main_frame = ctk.CTkFrame(root)
        main_frame.pack(fill="both", expand=True, padx=24, pady=24)

        header = ctk.CTkFrame(main_frame, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(header, text="Link2Vid", font=("Arial", 24, "bold")).pack(anchor="w")

        action_bar = ctk.CTkFrame(main_frame)
        action_bar.pack(fill="x", pady=(0, 16))
        self.url_entry = ctk.CTkEntry(
            action_bar,
            font=font_med,
            placeholder_text="Paste a video or playlist URL…",
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=10)
        self.url_entry.bind('<KeyRelease>', self.update_button_states)
        self.url_entry.bind('<Return>', lambda _event: self.fetch_videos())
        self.fetch_btn = ctk.CTkButton(
            action_bar,
            text='Fetch',
            command=self.fetch_videos,
            font=font_med,
            height=36,
            width=140,
            state="disabled",
        )
        self.fetch_btn.pack(side="right", padx=(0, 10))

        self.results_frame = ctk.CTkFrame(main_frame)
        self.results_frame.pack(fill="both", expand=True, pady=(0, 16))
        self.results_scroll = ctk.CTkScrollableFrame(self.results_frame)
        self.results_scroll.pack(fill="both", expand=True, padx=6, pady=6)
        self.results_state = ctk.CTkLabel(
            self.results_scroll,
            text="Paste a URL and fetch results.",
            font=font_med,
        )
        self.results_state.pack(pady=16)
        self.load_more_button = ctk.CTkButton(
            self.results_scroll,
            text="Load more",
            command=self.load_more,
            height=32,
        )

        self.batch_bar = BatchBar(
            main_frame,
            on_select_all=self.select_all_entries,
            on_select_none=self.select_no_entries,
            on_download_selected=self.start_batch_media,
            on_transcript_selected=self.start_batch_transcript,
            on_cancel=self.cancel_batch,
        )
        self.batch_bar.pack_forget()

        if self.dev_defaults.get('cookies_path'):
            self.cookies_path = self.dev_defaults.get('cookies_path')

        self.progress = ctk.DoubleVar()
        self.progress_bar = ctk.CTkProgressBar(main_frame, variable=self.progress, height=12)
        self.progress_bar.pack(fill="x", pady=(0, 12))
        self.progress_bar.set(0)

        self.footer = FooterBar(
            main_frame,
            output_path=self.output_path,
            cookies_path=self.cookies_path or "",
            on_change_output=self.browse_folder,
            on_change_cookies=self.browse_cookies,
            on_copy_diagnostics=self.copy_diagnostics,
        )
        self.footer.pack(fill="x", pady=(0, 8))

        self.log_drawer = LogDrawer(main_frame, collapsed=True)
        self.log_drawer.pack(fill="x")

        from ..core.runtime import startup_summary

        self.log(startup_summary())
        self.log(
            "UI runtime: "
            f"pid={os.getpid()} exe={sys.executable} cwd={os.getcwd()} "
            f"source={os.path.abspath(__file__)} output={self.output_path} "
            f"debug_log={self.debug_log_path}"
        )

        if self.dev_defaults.get('use_defaults') and self.dev_defaults.get('default_url'):
            self.url_entry.insert(0, self.dev_defaults['default_url'])
        else:
            self._populate_url_from_clipboard_if_valid()

        self.root.after(100, self.process_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_button_states()
        self.log(f"yt-dlp version: {get_yt_dlp_version()}")
        self.check_ffmpeg()
        self.check_js_runtime()
        self._probe_whisper_models()
        self.log("Tip: Link2Vid tries browser cookies automatically for auth-like failures before asking for cookies.txt.")

    def _populate_url_from_clipboard_if_valid(self) -> None:
        try:
            self.root.update_idletasks()
            clip = self.root.clipboard_get()
        except TclError:
            return
        url = url_from_clipboard_text(clip)
        if not url:
            return
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url)

    # ──────────────────────────────────────────────────────────
    # Utility / Logging
    # ──────────────────────────────────────────────────────────
    def _append_log(self, message):
        if not message:
            return
        self.log_history.append(message)
        if len(self.log_history) > self.log_max:
            self.log_history = self.log_history[-self.log_max:]
        self._append_debug_log(message)
        if hasattr(self, "log_drawer"):
            self.log_drawer.append(message)

    def _default_debug_log_path(self) -> str:
        downloads = Path.home() / "Downloads"
        parent = downloads if downloads.exists() else Path(tempfile.gettempdir())
        return str(parent / "link2vid-debug.log")

    def _append_debug_log(self, message: str) -> None:
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            with open(self.debug_log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"{timestamp} {message}\n")
        except Exception:
            return

    def log(self, message):
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("log", message))
            return
        self._append_log(message)

    def run_on_ui_thread(self, func):
        if threading.get_ident() == self.main_thread_id:
            return func()
        done = threading.Event()
        result = {}

        def wrapper():
            try:
                result["value"] = func()
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        self.ui_queue.put(("call", wrapper))
        done.wait()
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def ui_confirm(self, title: str, message: str) -> bool:
        return bool(self.run_on_ui_thread(lambda: messagebox.askyesno(title, message)))

    def ui_ffmpeg_fallback(self, fmt_label: str) -> str:
        message = (
            f"ffmpeg is required to merge audio/video for '{fmt_label}'.\n\n"
            "Yes: Switch to 'Best' (single file, most compatible)\n"
            "No: Continue anyway (may fail or be video-only)\n"
            "Cancel: Abort download."
        )
        result = self.run_on_ui_thread(lambda: messagebox.askyesnocancel("ffmpeg missing", message))
        if result is None:
            return "cancel"
        if result:
            return "fallback"
        return "continue"

    def confirm_cookies_retry(self, context: str) -> bool:
        noun = "transcript" if context == "transcript" else "video"
        message = (
            f"This {noun} may require cookies (age/consent/bot checks).\n"
            "Automatic browser-cookie retry did not finish successfully.\n\n"
            "Select a cookies.txt file to retry?"
        )
        policy = self.batch_prompt_policy
        if policy is None:
            return self.ui_confirm("Cookies required", message)
        with policy.lock:
            if policy.cookies_answer is None:
                policy.cookies_answer = self.ui_confirm("Cookies required", message)
            return policy.cookies_answer

    def confirm_format_fallback(self) -> bool:
        message = (
            "The selected format is not available for this video.\n"
            "Retry with Best (single file)?"
        )
        policy = self.batch_prompt_policy
        if policy is None:
            return self.ui_confirm("Format unavailable", message)
        with policy.lock:
            if policy.format_fallback_answer is None:
                policy.format_fallback_answer = self.ui_confirm("Format unavailable", message)
            return policy.format_fallback_answer

    def confirm_ffmpeg_fallback(self, fmt_label: str) -> str:
        policy = self.batch_prompt_policy
        if policy is None:
            return self.ui_ffmpeg_fallback(fmt_label)
        with policy.lock:
            if policy.ffmpeg_answer is None:
                policy.ffmpeg_answer = self.ui_ffmpeg_fallback(fmt_label)
            return policy.ffmpeg_answer

    def ui_warn(self, title: str, message: str) -> None:
        self.run_on_ui_thread(lambda: messagebox.showwarning(title, message))

    def ui_offer_copy_diagnostics(self) -> bool:
        return bool(
            self.run_on_ui_thread(
                lambda: messagebox.askyesno(
                    "Copy Diagnostics",
                    "Copy Link2Vid diagnostics to the clipboard now?",
                )
            )
        )

    def ui_select_cookies(self) -> None:
        self.run_on_ui_thread(self.browse_cookies)

    def ui_prompt_for_credentials(self, url: str):
        return self.run_on_ui_thread(lambda: self.prompt_for_credentials(url))

    def process_ui_queue(self):
        try:
            while True:
                action, payload = self.ui_queue.get_nowait()
                if action == "call":
                    payload()
                elif action == "log":
                    self._append_log(payload)
                elif action == "progress":
                    self.progress.set(payload)
                    self.progress_bar.set(payload)
                elif action == "results":
                    self.video_entries = self._sanitize_entries(payload)
                    self.entries_generation += 1
                    self.populate_cards()
                elif action == "results_state":
                    self.set_results_state(payload)
                elif action == "card_status":
                    card, status, state = payload
                    card.set_status(status, state=state)
                elif action == "card_progress":
                    card, value = payload
                    card.set_progress(value)
                elif action == "card_busy":
                    card, busy = payload
                    self._set_card_busy(card, busy)
                elif action == "verified_media":
                    self._apply_verified_media_event(payload)
                elif action in (
                    "whisper_snapshot",
                    "card_progress_mode",
                    "whisper_card_progress",
                    "whisper_status",
                    "whisper_busy",
                ):
                    self._consume_whisper_ui_event(action, payload)
                elif action == "fetch_done":
                    self.is_fetching = False
                    self.update_button_states()
                elif action == "thumbnail":
                    card, image = payload
                    if not card.winfo_exists():
                        continue
                    if image is not None:
                        card.set_thumbnail_image(image)
                    else:
                        entry = self.card_entries.get(card)
                        source_label = self.get_source_label(entry) if entry else "default"
                        placeholder = self.get_placeholder_image(source_label)
                        card.set_thumbnail_image(self.make_ctk_image(placeholder))
        except queue.Empty:
            pass
        self.root.after(100, self.process_ui_queue)

    def _probe_whisper_models(self) -> None:
        """Lazily probe exact Faster Whisper aliases; never download or load a model.

        Import/probe failure leaves an empty alias list so the rest of the app
        still launches. Instantiating ``WhisperService`` itself does not import
        native dependencies; probing goes through the injectable adapter.
        """
        try:
            aliases = [
                str(alias)
                for alias in self.whisper_service.list_available_models()
                if alias
            ]
        except Exception as exc:
            self.whisper_models = []
            self._log_whisper_safely(
                "Whisper unavailable: dependency import/probe failed "
                f"({type(exc).__name__}: {exc}). Download and provider Transcript "
                "actions remain available."
            )
            return

        self.whisper_models = aliases
        if aliases:
            self._log_whisper_safely(
                f"Whisper models available: {len(aliases)} "
                f"(default preference: large-v3 when present)."
            )
        else:
            self._log_whisper_safely(
                "Whisper model probe returned no aliases; Whisper controls stay disabled."
            )

    def _log_whisper_safely(self, message: str) -> None:
        """Best-effort main-thread logging that cannot break queue consumption."""
        try:
            self.log(message)
        except Exception:
            try:
                self._append_debug_log(message)
            except Exception:
                pass

    def _consume_whisper_ui_event(self, action: str, payload) -> None:
        """Apply one Whisper UI event without aborting the surrounding queue drain."""
        try:
            if action == "whisper_snapshot":
                self._apply_whisper_snapshot_event(payload)
            elif action == "card_progress_mode":
                self._apply_card_progress_mode_event(payload)
            elif action == "whisper_card_progress":
                self._apply_whisper_card_progress_event(payload)
            elif action == "whisper_status":
                self._apply_whisper_status_event(payload)
            elif action == "whisper_busy":
                self._apply_whisper_busy_event(payload)
        except Exception as exc:
            self._log_whisper_safely(
                f"Dropped {action} event after widget/session failure "
                f"({type(exc).__name__}: {exc})."
            )

    def _whisper_event_is_current(self, card, request_id: str) -> bool:
        """Return True when a Whisper queue event may mutate session/widget state."""
        if not request_id or card is None:
            return False
        if not any(session_card is card for session_card in self.card_entries):
            return False
        try:
            if not card.winfo_exists():
                return False
        except Exception as exc:
            self._log_whisper_safely(
                "Dropped Whisper event because its card widget is unavailable "
                f"({type(exc).__name__}: {exc})."
            )
            return False
        return self.whisper_request_id_by_card.get(card) == request_id

    def _apply_whisper_snapshot_event(self, payload) -> None:
        """Apply ``whisper_snapshot`` on the main thread only.

        Payload shape: ``(card, request_id, snapshot)`` where ``snapshot`` is a
        ``WhisperRunSnapshot``. Stale/mismatched/destroyed events are dropped.
        This is the sole mutator of ``whisper_runs_by_card``.
        """
        try:
            card, request_id, snapshot = payload
        except (TypeError, ValueError):
            return
        if not self._whisper_event_is_current(card, request_id):
            return
        if not isinstance(snapshot, WhisperRunSnapshot):
            return
        if snapshot.request_id != request_id:
            return
        self.whisper_runs_by_card[card] = snapshot

    def _apply_card_progress_mode_event(self, payload) -> None:
        """Apply ``card_progress_mode`` on the main thread only.

        Payload shape: ``(card, request_id, mode)`` where ``mode`` is
        ``determinate``, ``indeterminate``, or ``reset``.
        """
        try:
            card, request_id, mode = payload
        except (TypeError, ValueError):
            return
        if not self._whisper_event_is_current(card, request_id):
            return
        normalized = str(mode).strip().lower()
        if normalized == "reset":
            card.reset_progress()
            return
        if normalized in ("determinate", "indeterminate"):
            card.set_progress_mode(normalized)

    def _apply_whisper_card_progress_event(self, payload) -> None:
        """Apply request-aware numeric Whisper card progress on the main thread.

        Payload shape: ``(card, request_id, value)`` where ``value`` is a
        fraction in ``[0, 1]`` from real yt-dlp acquisition progress only.
        Stale/mismatched/destroyed events are dropped without fabricating values.
        """
        try:
            card, request_id, value = payload
        except (TypeError, ValueError):
            return
        if not self._whisper_event_is_current(card, request_id):
            return
        try:
            progress = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        card.set_progress(progress)

    def _apply_whisper_status_event(self, payload) -> None:
        """Apply ``whisper_status`` on the main thread only.

        Payload shape: ``(card, request_id, status, state)``. Narrow status
        helper for Whisper workers; same request/card guards as snapshots.
        """
        try:
            card, request_id, status, state = payload
        except (TypeError, ValueError):
            return
        if not self._whisper_event_is_current(card, request_id):
            return
        card.set_status(status, state=state)

    def _apply_whisper_busy_event(self, payload) -> None:
        """Apply request-aware ``whisper_busy`` on the main thread only.

        Payload shape: ``(card, request_id, busy)``. Clearing busy is ignored
        when ``request_id`` no longer matches so a stale worker cannot re-enable
        a newer card action. Setting busy still requires a current request.
        """
        try:
            card, request_id, busy = payload
        except (TypeError, ValueError):
            return
        if not self._whisper_event_is_current(card, request_id):
            return
        self._set_card_busy(card, bool(busy))

    def queue_whisper_snapshot(self, card, request_id: str, snapshot: WhisperRunSnapshot) -> None:
        """Enqueue or apply an immutable Whisper run snapshot for a card."""
        payload = (card, request_id, snapshot)
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("whisper_snapshot", payload))
            return
        self._consume_whisper_ui_event("whisper_snapshot", payload)

    def queue_card_progress_mode(self, card, request_id: str, mode: str) -> None:
        """Enqueue or apply explicit card progress mode for a Whisper request."""
        payload = (card, request_id, mode)
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("card_progress_mode", payload))
            return
        self._consume_whisper_ui_event("card_progress_mode", payload)

    def queue_whisper_card_progress(self, card, request_id: str, value: float) -> None:
        """Enqueue or apply request-aware numeric Whisper card progress.

        Workers must use this instead of ``queue_card_progress`` so stale
        acquisition updates cannot mutate a newer card action.
        """
        try:
            progress = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        payload = (card, request_id, progress)
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("whisper_card_progress", payload))
            return
        self._consume_whisper_ui_event("whisper_card_progress", payload)

    def queue_whisper_status(
        self,
        card,
        request_id: str,
        status: str,
        state: str | None = None,
    ) -> None:
        """Enqueue or apply a Whisper-scoped card status update.

        Active Whisper phases use the existing ``downloading`` card state.
        """
        payload = (card, request_id, status, state)
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("whisper_status", payload))
            return
        self._consume_whisper_ui_event("whisper_status", payload)

    def queue_whisper_busy(self, card, request_id: str, busy: bool) -> None:
        """Enqueue or apply request-aware busy state for a Whisper run.

        Workers must use this instead of ``queue_card_busy`` so a stale
        terminal clear cannot overwrite a newer action's busy ownership.
        """
        payload = (card, request_id, bool(busy))
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("whisper_busy", payload))
            return
        self._consume_whisper_ui_event("whisper_busy", payload)

    def _card_in_session(self, card) -> bool:
        if card is None or not any(
            session_card is card for session_card in self.card_entries
        ):
            return False
        try:
            return bool(card.winfo_exists())
        except Exception:
            return False

    def _next_verified_media_epoch(self, card) -> int:
        epoch = self._verified_media_epoch_by_card.get(card, 0) + 1
        self._verified_media_epoch_by_card[card] = epoch
        return epoch

    def _apply_verified_media_event(self, payload) -> None:
        """Apply a verified-media queue event on the main thread only.

        Payload shape: ``(card, media, epoch)`` where ``media`` is
        ``VerifiedMedia | None``. ``None`` clears the card association.
        Events for cards no longer in ``card_entries`` / destroyed widgets are
        dropped. Epoch mismatches drop late associations from superseded actions.
        """
        try:
            card, media, epoch = payload
        except (TypeError, ValueError):
            return
        if not self._card_in_session(card):
            return
        if epoch != self._verified_media_epoch_by_card.get(card):
            return
        if media is None:
            self.verified_media_by_card.pop(card, None)
            return
        if not isinstance(media, VerifiedMedia):
            return
        self.verified_media_by_card[card] = media

    def invalidate_verified_media(self, card) -> int:
        """Clear a card's verified-media association before a new media action.

        Must run on the main thread (never a background write). Returns the
        epoch workers must present when associating a new result. Prior queued
        associations with a stale epoch are dropped by ``_apply_verified_media_event``.
        """
        if card is None:
            return 0
        if threading.get_ident() != self.main_thread_id:
            raise RuntimeError("invalidate_verified_media must run on the UI thread")
        epoch = self._next_verified_media_epoch(card)
        self.verified_media_by_card.pop(card, None)
        return epoch

    def queue_verified_media(self, card, media: VerifiedMedia | None, epoch: int) -> None:
        """Enqueue or apply an exact-card verified-media association."""
        payload = (card, media, epoch)
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("verified_media", payload))
            return
        self._apply_verified_media_event(payload)

    def set_progress(self, value):
        value = max(0.0, min(1.0, value))
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("progress", value))
            return
        self.progress.set(value)
        self.progress_bar.set(value)

    def queue_results_state(self, text: str):
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("results_state", text))
            return
        self.set_results_state(text)

    def queue_card_status(self, card, status: str, state: str | None = None):
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("card_status", (card, status, state)))
            return
        card.set_status(status, state=state)

    def queue_card_progress(self, card, value: float):
        value = max(0.0, min(1.0, value))
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("card_progress", (card, value)))
            return
        card.set_progress(value)

    def _set_card_busy(self, card, busy: bool):
        if not card or not card.winfo_exists():
            return
        if busy:
            self.busy_cards.add(card)
        else:
            self.busy_cards.discard(card)
        card.set_actions_enabled(bool(self.output_path) and card not in self.busy_cards)

    def queue_card_busy(self, card, busy: bool):
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("card_busy", (card, busy)))
            return
        self._set_card_busy(card, busy)

    def format_error(self, stage, err):
        err_text = str(err) or "Unknown error"
        reason, hint = classify_error(err_text)
        self.last_error_reason = reason
        reason_text = f" (Reason: {reason})" if reason else ""
        return f"[{stage}] {type(err).__name__}: {err_text}{reason_text}{hint}"

    def error_hint(self, err_text):
        _reason, hint = classify_error(err_text)
        return hint

    def log_error(self, stage, err):
        msg = self.format_error(stage, err)
        self.last_error = msg
        self.log(msg)
        self._warn_for_error(err)

    def _warn_for_error(self, err: Exception) -> None:
        guidance = get_error_guidance(str(err) or "")
        if not guidance:
            return
        key, title, message = guidance
        if key in self.shown_error_guidance:
            return
        self.shown_error_guidance.add(key)
        self.ui_warn(title, message)
        if self.ui_offer_copy_diagnostics():
            self.run_on_ui_thread(self.copy_diagnostics)

    def check_ffmpeg(self):
        self.ffmpeg_path = shutil.which("ffmpeg")
        self.ffmpeg_available = bool(self.ffmpeg_path)
        if not self.ffmpeg_available:
            self.log("ffmpeg not found on PATH. Some formats may fail or be video-only.")

    def check_js_runtime(self):
        runtimes = ["deno", "node", "bun"]
        self.js_runtimes = [rt for rt in runtimes if shutil.which(rt)]
        self.js_runtime_available = bool(self.js_runtimes)
        if not self.js_runtimes:
            self.log(
                "No JavaScript runtime detected (deno/node/bun). "
                "Some downloads may fail without one."
            )

    def _latest_whisper_diagnostics_input(self) -> WhisperDiagnosticsInput | None:
        """Return Whisper fields for Copy Diagnostics from the latest live run only.

        Selection is deterministic: only ``latest_whisper_card`` is considered.
        The card must still be in the current session, its widget must exist, and
        the stored snapshot ``request_id`` must match ``whisper_request_id_by_card``.
        Never falls back to another card or an older mismatched run.
        """
        card = self.latest_whisper_card
        if card is None:
            return None
        if not any(session_card is card for session_card in self.card_entries):
            return None
        try:
            if not card.winfo_exists():
                return None
        except Exception:
            return None
        request_id = self.whisper_request_id_by_card.get(card)
        if not request_id:
            return None
        snapshot = self.whisper_runs_by_card.get(card)
        if not isinstance(snapshot, WhisperRunSnapshot):
            return None
        if snapshot.request_id != request_id:
            return None
        phase = snapshot.phase
        phase_value = phase.value if isinstance(phase, WhisperPhase) else str(phase)
        return WhisperDiagnosticsInput(
            model_name=snapshot.model_name,
            phase=phase_value,
            cache_root=snapshot.cache_root,
            source_kind=snapshot.source_kind,
            effective_device=snapshot.effective_device,
            fallback_used=snapshot.fallback_used,
            error_reason=snapshot.error_reason,
        )

    def copy_diagnostics(self):
        url = self.url_entry.get().strip()
        cookies_mode = getattr(self.download_manager, "last_cookies_mode", "none") or "none"
        cookies_browser = getattr(self.download_manager, "last_cookies_browser", None) or "n/a"
        js_runtime_used = getattr(self.download_manager, "last_js_runtime", None) or "n/a"
        js_runtime_path = getattr(self.download_manager, "last_js_runtime_path", None) or "n/a"
        remote_components = getattr(self.download_manager, "last_remote_components", None) or []
        js_runtime = ", ".join(self.js_runtimes) if getattr(self, "js_runtimes", None) else None
        lines = build_diagnostics(
            url=url,
            action_kind=self.last_action_kind,
            selected_title=self.last_selected_title,
            selected_format=self.last_selected_format,
            output_path=self.output_path,
            debug_log_path=self.debug_log_path,
            transcript_source=self.last_transcript_source,
            transcript_languages=self.last_transcript_languages,
            yt_dlp_version=get_yt_dlp_version(),
            ffmpeg_path=self.ffmpeg_path,
            js_runtime=js_runtime,
            js_runtime_used=js_runtime_used,
            js_runtime_path=js_runtime_path,
            remote_components=remote_components,
            cookies_mode=cookies_mode,
            cookies_browser=cookies_browser,
            last_error=self.last_error,
            last_error_reason=self.last_error_reason,
            log_history=self.log_history,
            whisper=self._latest_whisper_diagnostics_input(),
        )
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.log("Diagnostics copied to clipboard.")

    def update_button_states(self, event=None):
        url = self.url_entry.get().strip()
        has_url = bool(url)
        has_folder = bool(self.output_path)
        fetch_state = "disabled" if self.is_fetching or not has_url else "normal"
        self.fetch_btn.configure(
            state=fetch_state,
            text="Fetching..." if self.is_fetching else "Fetch",
        )
        self.update_card_buttons(has_folder)
        if hasattr(self, "batch_bar"):
            self._refresh_batch_bar()

    # ──────────────────────────────────────────────────────────
    # Core flow
    # ──────────────────────────────────────────────────────────
    def fetch_videos(self):
        if self.is_fetching:
            return
        url = self.url_entry.get().strip()
        # Normalize and trim URL text before fetch.
        norm_url = normalize_url(url)
        if norm_url != url:
            self.log(f"Using URL: {norm_url}")
            url = norm_url
        self.last_url = url
        if not url:
            self.ui_warn("Input Error", "Please enter a URL.")
            return

        self.is_fetching = True
        self.clear_results()
        self.set_results_state("Fetching...")
        self.update_button_states()

        self.fetch_future = self.executor.submit(self._fetch_videos_worker, url)

    def _fetch_videos_worker(self, url: str) -> None:
        try:
            username, password = dev_credentials_for_url(url, self.dev_defaults)

            outcome = self.fetcher.fetch(url, username, password)
            if isinstance(outcome, NeedsCookies):
                error_to_log = outcome.error
                if isinstance(outcome.error, CookiesRequiredError) and outcome.error.original:
                    error_to_log = outcome.error.original
                self.log_error("yt-dlp", error_to_log)
                if self.ui_confirm(
                    "Cookies required",
                    "This video may require cookies (age/consent/bot checks).\n"
                    "Automatic browser-cookie retry did not finish successfully.\n\n"
                    "Select a cookies.txt file to retry?",
                ):
                    self.ui_select_cookies()
                    if self.cookies_path:
                        outcome = self.fetcher.fetch(url, username, password)
                    else:
                        self.log("No cookies.txt selected.")
                        self.ui_queue.put(("results", []))
                        return
                else:
                    self.log("Cookies selection declined.")
                    self.ui_queue.put(("results", []))
                    return
                if isinstance(outcome, NeedsCookies):
                    self.log("Cookies retry did not resolve the fetch error.")
                    self.ui_queue.put(("results", []))
                    return

            if isinstance(outcome, FetchResults):
                if outcome.error:
                    self.log_error("yt-dlp", outcome.error)
                self.ui_queue.put(("results", outcome.entries))
                return

            if isinstance(outcome, DirectHlsFound):
                if outcome.error:
                    self.log("[Fallback] yt-dlp native extraction failed; using direct HLS fallback.")
                entries = build_media_entries(
                    [outcome.result.playlist_url],
                    page_title=title_from_page_url(url),
                    headers=outcome.result.headers,
                )
                self.ui_queue.put(("results", entries))
                return

            if isinstance(outcome, NeedsSelenium):
                if outcome.error:
                    self.log_error("yt-dlp", outcome.error)
                if self.ui_confirm(
                    "Fallback",
                    "Automatic scrape failed. Try browser automation?\n\n"
                    "Link2Vid will open the target site in Chrome, log in with the "
                    "credentials you provide, then look for a playable media URL.",
                ):
                    username, password = self.ui_prompt_for_credentials(url)
                    if not username or not password:
                        self.log("No credentials provided.")
                        self.ui_queue.put(("results", []))
                        return
                    entries = self.selenium_fallback(url, username, password)
                    if entries:
                        self.ui_queue.put(("results", entries))
                        return
                    self.log("No media found with Selenium.")

                self.ui_queue.put(("results", []))
                return

            if isinstance(outcome, FetchError):
                self.log_error("Fetch", outcome.error)

            self.ui_queue.put(("results", []))
        finally:
            self.ui_queue.put(("fetch_done", None))

    # ──────────────────────────────────────────────────────────
    # Direct HLS helper
    # ──────────────────────────────────────────────────────────
    def _format_progress_time(self, seconds: float | None) -> str:
        if seconds is None:
            return "0:00"
        return format_duration_text(seconds) or "0:00"

    def _ffmpeg_progress_hook(
        self,
        fraction: float,
        elapsed: float | None,
        duration: float | None,
        *,
        last_logged_pct: list[int],
    ) -> None:
        self.set_progress(fraction)
        pct, elapsed_text, total_text = ffmpeg_progress_display(fraction, elapsed, duration)
        if duration and duration > 0:
            self.queue_results_state(
                f"Downloading via ffmpeg... {pct}% ({elapsed_text} / {total_text})"
            )
            if pct >= last_logged_pct[0] + 10 or pct >= 100:
                last_logged_pct[0] = pct - (pct % 10)
                self.log(f"[ffmpeg] {pct}% ({elapsed_text} / {total_text})")
        elif elapsed is not None:
            self.queue_results_state(f"Downloading via ffmpeg... {elapsed_text} elapsed")
            if pct >= last_logged_pct[0] + 10:
                last_logged_pct[0] = pct - (pct % 10)
                self.log(f"[ffmpeg] {elapsed_text} elapsed")

    def clear_results(self):
        if self.batch_run is not None:
            self.batch_run.cancel.set()
            self.batch_bar.set_status("Stopping after current item…")
        for card in self.cards:
            card.destroy()
        self.cards = []
        self.card_entries = {}
        self.selected_entry_indexes.clear()
        self.entry_format_by_index.clear()
        self.entry_transcript_by_index.clear()
        self.card_index_by_card.clear()
        self.card_by_index.clear()
        self.busy_cards.clear()
        self.verified_media_by_card.clear()
        self._verified_media_epoch_by_card.clear()
        self.whisper_runs_by_card.clear()
        self.whisper_request_id_by_card.clear()
        self.latest_whisper_card = None
        self.rendered_count = 0
        if self.load_more_button.winfo_manager():
            self.load_more_button.pack_forget()
        self.batch_bar.pack_forget()

    def _sanitize_entries(self, entries) -> list[dict]:
        raw_entries = list(entries or [])
        sanitized = [entry for entry in raw_entries if isinstance(entry, dict)]
        dropped = len(raw_entries) - len(sanitized)
        if dropped:
            suffix = "entry" if dropped == 1 else "entries"
            self.log(f"Dropped {dropped} unusable {suffix}.")
        return sanitized

    def set_results_state(self, text: str):
        if text:
            self.results_state.configure(text=text)
            if not self.results_state.winfo_manager():
                self.results_state.pack(pady=16)
        else:
            if self.results_state.winfo_manager():
                self.results_state.pack_forget()

    def format_duration(self, duration):
        return format_duration_text(duration)

    def build_metadata(self, entry):
        parts = []
        site = entry.get('extractor_key') or entry.get('extractor')
        if site:
            parts.append(site.replace('_', ' ').title())
        duration = self.format_duration(entry.get('duration'))
        if duration:
            parts.append(duration)
        uploader = entry.get('uploader') or entry.get('channel') or entry.get('uploader_id') or entry.get('channel_id')
        if uploader:
            parts.append(uploader)
        return " · ".join(parts)

    def populate_cards(self):
        self.clear_results()
        if not self.video_entries:
            self.set_results_state("No videos found or unsupported site.")
            self.log("No videos found.")
            return
        self.selected_entry_indexes = set(range(len(self.video_entries)))
        self.entry_format_by_index.clear()
        self.entry_transcript_by_index.clear()
        self.batch_bar.pack(fill="x", pady=(0, 12), before=self.results_frame)
        self._refresh_batch_bar()
        self.set_results_state("")
        self.render_next_batch()
        self.log(f"Found {len(self.video_entries)} video(s).")

    def render_next_batch(self):
        if not self.video_entries:
            return
        if self.load_more_button.winfo_manager():
            self.load_more_button.pack_forget()

        start = self.rendered_count
        end = min(start + self.batch_size, len(self.video_entries))
        for index, entry in enumerate(self.video_entries[start:end], start=start):
            title = entry.get('title', 'No Title')
            metadata = self.build_metadata(entry)
            source_label = self.get_source_label(entry)
            transcript_options = self.get_transcript_options(entry)
            card = VideoCard(
                self.results_scroll,
                title=title,
                metadata=metadata,
                format_options=self.format_options,
                transcript_options=transcript_options,
                whisper_models=self.whisper_models,
                on_download=self.handle_card_download,
                on_transcript=self.handle_card_transcript,
                on_whisper=self.handle_card_whisper,
                selected=index in self.selected_entry_indexes,
                on_selection_change=self.handle_card_selection_change,
                on_format_change=self.handle_card_format_change,
                on_transcript_change=self.handle_card_transcript_change,
            )
            card.pack(fill="x", padx=8, pady=8)
            if transcript_options == []:
                card.set_status("No transcript tracks", state="ready")
            self.cards.append(card)
            self.card_entries[card] = entry
            self.card_index_by_card[card] = index
            self.card_by_index[index] = card
            placeholder = self.get_placeholder_image(source_label)
            card.set_thumbnail_image(self.make_ctk_image(placeholder))
            self.queue_thumbnail(card, entry, source_label)

        self.rendered_count = end
        if self.rendered_count < len(self.video_entries):
            self.load_more_button.pack(pady=(8, 12))

        self.update_card_buttons(bool(self.output_path))

    def handle_card_selection_change(self, card, selected: bool) -> None:
        index = self.card_index_by_card.get(card)
        if index is None:
            return
        if selected:
            self.selected_entry_indexes.add(index)
        else:
            self.selected_entry_indexes.discard(index)
        self._refresh_batch_bar()

    def select_all_entries(self) -> None:
        self.selected_entry_indexes = set(range(len(self.video_entries)))
        self._refresh_batch_bar()

    def select_no_entries(self) -> None:
        self.selected_entry_indexes.clear()
        self._refresh_batch_bar()

    def _selected_indexes_ordered(self) -> list[int]:
        return sorted(self.selected_entry_indexes)

    def _refresh_batch_bar(self) -> None:
        for index, card in self.card_by_index.items():
            card.set_selected(index in self.selected_entry_indexes, notify=False)
        self.batch_bar.set_selection_summary(
            len(self.selected_entry_indexes),
            len(self.video_entries),
        )
        self.batch_bar.set_enabled(
            bool(self.output_path)
            and bool(self.video_entries)
            and not self.is_fetching
            and self.batch_run is None
        )

    def handle_card_format_change(self, card, format_id: str) -> None:
        index = self.card_index_by_card.get(card)
        if index is not None:
            self.entry_format_by_index[index] = format_id

    def handle_card_transcript_change(self, card, track) -> None:
        index = self.card_index_by_card.get(card)
        if index is not None:
            self.entry_transcript_by_index[index] = track

    def format_for_index(self, index: int) -> str:
        return self.entry_format_by_index.get(index, self.format_options[0]["format"])

    def transcript_track_for_index(self, index: int):
        return self.entry_transcript_by_index.get(index)

    def entry_url_for_entry(self, entry) -> str | None:
        resolved = entry_watch_url(entry)
        if resolved:
            return resolved
        if len(self.video_entries) <= 1:
            fallback = self.url_entry.get().strip()
            return fallback or None
        return None

    def get_transcript_options(self, entry):
        if not isinstance(entry, dict):
            return None
        has_transcript_metadata = "subtitles" in entry or "automatic_captions" in entry
        if not has_transcript_metadata:
            return None
        return self.download_manager.transcript_tracks_from_info(entry)

    def load_more(self):
        self.render_next_batch()

    def queue_thumbnail(self, card, entry, source_label: str) -> None:
        url = entry.get("thumbnail") or entry.get("thumbnail_url")
        if not url:
            placeholder = self.get_placeholder_image(source_label)
            card.set_thumbnail_image(self.make_ctk_image(placeholder))
            return

        def on_ready(image):
            def apply():
                if image is not None:
                    return self.make_ctk_image(image)
                return None

            if threading.get_ident() != self.main_thread_id:
                ctk_image = self.run_on_ui_thread(apply)
            else:
                ctk_image = apply()
            self.ui_queue.put(("thumbnail", (card, ctk_image)))

        self.thumbnail_loader.submit(url, self.thumbnail_size, on_ready)

    def make_ctk_image(self, image):
        return ctk.CTkImage(light_image=image, dark_image=image, size=self.thumbnail_size)

    def get_default_output_path(self) -> str:
        downloads = Path.home() / "Downloads"
        if downloads.exists():
            return str(downloads)
        return str(Path.home())

    def get_source_label(self, entry) -> str:
        url = entry.get("webpage_url") or ""
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            bucket = sum(ord(char) for char in host) % 3
            return ("site_a", "site_b", "site_c")[bucket]
        return "default"

    def get_placeholder_image(self, source_label: str):
        key = (source_label, self.thumbnail_size)
        cached = self.placeholder_cache.get(key)
        if cached is not None:
            return cached

        label, colors = self.thumbnail_styles.get(source_label, self.thumbnail_styles["default"])
        image = Image.new("RGB", self.thumbnail_size, colors[0])
        draw = ImageDraw.Draw(image)
        self._draw_gradient(draw, self.thumbnail_size, colors)
        image = image.filter(ImageFilter.GaussianBlur(radius=0.6))

        draw = ImageDraw.Draw(image)
        font = self._get_placeholder_font()
        text = label.upper()
        text_box = draw.textbbox((0, 0), text, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        x = (self.thumbnail_size[0] - text_width) / 2
        y = (self.thumbnail_size[1] - text_height) / 2
        draw.text((x, y), text, fill="#ffffff", font=font)

        self.placeholder_cache[key] = image
        return image

    def _draw_gradient(self, draw, size, colors):
        width, height = size
        start, end = colors
        start_rgb = self._hex_to_rgb(start)
        end_rgb = self._hex_to_rgb(end)
        for y in range(height):
            ratio = y / max(1, height - 1)
            r = int(start_rgb[0] + (end_rgb[0] - start_rgb[0]) * ratio)
            g = int(start_rgb[1] + (end_rgb[1] - start_rgb[1]) * ratio)
            b = int(start_rgb[2] + (end_rgb[2] - start_rgb[2]) * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

    def _hex_to_rgb(self, value: str):
        value = value.lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

    def _get_placeholder_font(self):
        if self.placeholder_font is not None:
            return self.placeholder_font
        try:
            self.placeholder_font = ImageFont.truetype("arial.ttf", 14)
        except Exception:
            self.placeholder_font = ImageFont.load_default()
        return self.placeholder_font

    # ──────────────────────────────────────────────────────────
    # Tk callbacks / UI helpers
    # ──────────────────────────────────────────────────────────
    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_path = folder
            self.footer.set_output_path(folder)
            self.log(f"Output folder changed: {folder}")
        self.update_button_states()

    def browse_cookies(self):
        path = filedialog.askopenfilename(title='Select cookies.txt',
                                          filetypes=[('Text files', '*.txt'), ('All files', '*.*')])
        if path:
            self.cookies_path = path
            self.footer.set_cookies_path(path)

    def update_card_buttons(self, has_folder: bool):
        for card in self.cards:
            card.set_actions_enabled(has_folder and card not in self.busy_cards)

    def _freeze_batch_items(self, kind: str) -> tuple[tuple[BatchItem, ...], int, int]:
        items: list[BatchItem] = []
        skipped = 0
        default_format_count = 0
        for index in self._selected_indexes_ordered():
            if index < 0 or index >= len(self.video_entries):
                skipped += 1
                self.log(f"Batch {kind} skipped: index out of range ({index}).")
                continue
            entry = self.video_entries[index]
            title = str(entry.get("title") or "Video")
            if entry.get("_ffmpeg_headers"):
                skipped += 1
                self.log(
                    f"Batch {kind} skipped: direct media not supported in batch ({title})."
                )
                continue
            url = self.entry_url_for_entry(entry)
            if url is None:
                skipped += 1
                self.log(f"Batch {kind} skipped: no resolvable URL ({title}).")
                continue
            if kind == "media":
                format_id = self.format_for_index(index)
                fmt_label = self.format_label_map.get(format_id, format_id)
                transcript_track = None
                if (
                    index not in self.card_by_index
                    and index not in self.entry_format_by_index
                ):
                    default_format_count += 1
            else:
                format_id = None
                fmt_label = None
                transcript_track = self.transcript_track_for_index(index)
            items.append(
                BatchItem(
                    index=index,
                    title=title,
                    url=url,
                    format_id=format_id,
                    fmt_label=fmt_label,
                    transcript_track=transcript_track,
                )
            )
        return tuple(items), skipped, default_format_count

    def start_batch_media(self) -> None:
        self._start_batch("media")

    def start_batch_transcript(self) -> None:
        self._start_batch("transcript")

    def _start_batch(self, kind: str) -> None:
        if not self.output_path:
            self.ui_warn("Folder Error", "Select a folder first.")
            return
        if not self.selected_entry_indexes:
            self.ui_warn("Batch", "Select at least one item first.")
            return
        if self.batch_run is not None:
            self.log("Batch start ignored: another batch is already running.")
            return
        if self.is_fetching:
            self.log("Batch start ignored: fetch is still running.")
            return
        items, skipped, default_count = self._freeze_batch_items(kind)
        if not items:
            self.log(f"Batch {kind} not started: no selected item can be processed.")
            self.batch_bar.set_status("No selected item can be processed.")
            return
        run = BatchRun(
            kind=kind,
            items=items,
            generation=self.entries_generation,
            folder=self.output_path,
            skipped=skipped,
            default_format_count=default_count,
        )
        self.batch_run = run
        self.batch_prompt_policy = BatchPromptPolicy()
        self.batch_bar.set_running(True)
        self.batch_bar.set_enabled(False)
        self.batch_bar.set_status(f"Starting batch {kind}…")
        self.log(
            f"Batch {kind} started: {len(self.selected_entry_indexes)} item(s) "
            f"selected of {len(self.video_entries)}."
        )
        threading.Thread(target=self._run_batch_worker, args=(run,), daemon=True).start()

    def _begin_batch_item(self, run: BatchRun, item: BatchItem):
        if run.generation != self.entries_generation:
            run.cancel.set()
            return None, "session replaced"
        card = self.card_by_index.get(item.index)
        if card is not None and card in self.busy_cards:
            return None, "card busy"
        if run.kind == "media":
            format_id = item.format_id or self.format_options[0]["format"]
            fmt_label = item.fmt_label or self.format_label_map.get(format_id, format_id)
            if not self.ffmpeg_available and "+" in format_id:
                choice = self.confirm_ffmpeg_fallback(fmt_label)
                if choice == "cancel":
                    return None, "ffmpeg declined"
                if choice == "fallback":
                    format_id = "best"
                    fmt_label = self.format_label_map.get(format_id, "Best (single file)")
                    self.log("ffmpeg missing: switching to Best (single file) for compatibility.")
                else:
                    self.log("ffmpeg missing: proceeding without merge support (may fail or be video-only).")
            media_epoch = self.invalidate_verified_media(card)
            if card is not None:
                self.queue_card_busy(card, True)
                self.queue_card_status(card, f"Downloading ({fmt_label})", state="downloading")
                self.queue_card_progress(card, 0)
            self.last_action_kind = "media"
            self.last_selected_format = format_id
            self.last_selected_label = fmt_label
            self.last_selected_title = item.title
            self.last_transcript_source = None
            self.last_transcript_languages = []
            self.set_progress(0)
            return (item.url, format_id, run.folder, card, fmt_label, media_epoch), None

        if card is not None:
            self.queue_card_busy(card, True)
            self.queue_card_status(card, "Downloading transcript", state="downloading")
            self.queue_card_progress(card, 0)
        self.last_action_kind = "transcript"
        self.last_selected_format = None
        self.last_selected_label = None
        self.last_selected_title = item.title
        self.last_transcript_source = None
        self.last_transcript_languages = []
        self.set_progress(0)
        return (item.url, run.folder, card, item.transcript_track), None

    def _record_batch_outcome(self, run: BatchRun, outcome: str, status: str) -> None:
        setattr(run, outcome, getattr(run, outcome) + 1)
        self.batch_bar.set_status(status)

    def _run_batch_worker(self, run: BatchRun) -> None:
        total = len(run.items)
        try:
            for position, item in enumerate(run.items, start=1):
                if run.cancel.is_set():
                    reason = self.run_on_ui_thread(
                        lambda: (
                            "session replaced"
                            if run.generation != self.entries_generation
                            else "cancelled"
                        )
                    )
                    remaining = total - position + 1
                    for skipped_item in run.items[position - 1:]:
                        self.log(
                            f"Batch {run.kind} skipped: {reason} ({skipped_item.title})."
                        )
                    self.run_on_ui_thread(
                        lambda count=remaining: setattr(run, "skipped", run.skipped + count)
                    )
                    break
                self.log(f"Batch {run.kind} [{position}/{total}]: {item.title}")
                self.run_on_ui_thread(
                    lambda p=position, title=item.title: self.batch_bar.set_status(
                        f"Batch {p}/{total} · {title}"
                    )
                )
                dispatch, skip_reason = self.run_on_ui_thread(
                    lambda current=item: self._begin_batch_item(run, current)
                )
                if dispatch is None:
                    self.log(
                        f"Batch {run.kind} [{position}/{total}] skipped: "
                        f"{skip_reason} ({item.title})."
                    )
                    self.run_on_ui_thread(
                        lambda: self._record_batch_outcome(
                            run, "skipped", f"Skipped · {item.title}"
                        )
                    )
                    continue
                try:
                    if run.kind == "media":
                        success = bool(self.download_video(*dispatch))
                    else:
                        success = bool(self.download_transcript(*dispatch))
                except Exception as exc:
                    self.log_error(f"Batch {run.kind}", exc)
                    card = dispatch[3] if len(dispatch) > 3 else None
                    if card is not None:
                        self.queue_card_status(card, "Failed", state="failed")
                        self.queue_card_busy(card, False)
                    success = False
                outcome = "completed" if success else "failed"
                if not success:
                    self.log(f"Batch {run.kind} [{position}/{total}] failed: {item.title}.")
                self.run_on_ui_thread(
                    lambda result=outcome: self._record_batch_outcome(
                        run, result, f"Batch {position}/{total} · {item.title}"
                    )
                )
        finally:
            self.run_on_ui_thread(lambda: self._finish_batch(run))

    def _finish_batch(self, run: BatchRun) -> None:
        summary = (
            f"Batch {run.kind} finished: {run.completed} complete, "
            f"{run.failed} failed, {run.skipped} skipped."
        )
        if self.batch_run is run:
            self.batch_run = None
        self.batch_prompt_policy = None
        self.batch_bar.set_running(False)
        self.batch_bar.set_status(summary)
        self.log(summary)
        if run.kind == "media" and run.default_format_count:
            default_label = self.format_options[0]["label"]
            self.log(
                f"Batch media: {run.default_format_count} item(s) used the session "
                f"default format ({default_label}) because their card was not rendered."
            )
        self._refresh_batch_bar()

    def cancel_batch(self) -> None:
        run = self.batch_run
        if run is None:
            return
        run.cancel.set()
        self.batch_bar.set_status("Stopping after current item…")

    def handle_card_download(self, card, format_id):
        entry = self.card_entries.get(card)
        if not entry:
            return
        folder = self.output_path
        if not folder:
            self.ui_warn('Folder Error', 'Select a folder first.')
            return
        url = self.entry_url_for_entry(entry)
        if url is None:
            self.log("Download failed: entry has no resolvable per-item URL.")
            self.queue_card_status(card, "Failed (No item URL)", state="failed")
            return
        ffmpeg_headers = entry.get("_ffmpeg_headers")
        if ffmpeg_headers:
            # Invalidate prior association on the main thread before any worker output.
            media_epoch = self.invalidate_verified_media(card)
            self._download_direct_media(
                card,
                url,
                ffmpeg_headers,
                entry.get('title', 'Video'),
                media_epoch=media_epoch,
            )
            return
        fmt_id = format_id or self.format_options[0]["format"]
        fmt_label = self.format_label_map.get(fmt_id, fmt_id)
        if not self.ffmpeg_available and '+' in fmt_id:
            choice = self.confirm_ffmpeg_fallback(fmt_label)
            if choice == "cancel":
                return
            if choice == "fallback":
                fmt_id = "best"
                fmt_label = self.format_label_map.get(fmt_id, "Best (single file)")
                self.log("ffmpeg missing: switching to Best (single file) for compatibility.")
            else:
                self.log("ffmpeg missing: proceeding without merge support (may fail or be video-only).")
        # Invalidate prior association on the main thread before any worker output.
        media_epoch = self.invalidate_verified_media(card)
        self.last_action_kind = "media"
        self.last_selected_format = fmt_id
        self.last_selected_label = fmt_label
        self.last_selected_title = entry.get('title', 'Video')
        self.last_transcript_source = None
        self.last_transcript_languages = []
        self.set_progress(0)
        self.queue_card_busy(card, True)
        self.queue_card_status(card, f"Downloading ({fmt_label})", state="downloading")
        self.queue_card_progress(card, 0)
        self.log(f"Starting download: {entry.get('title', 'Video')} ({fmt_label})")
        threading.Thread(
            target=self.download_video,
            args=(url, fmt_id, folder, card, fmt_label, media_epoch),
            daemon=True,
        ).start()

    def handle_card_transcript(self, card, selected_transcript=None):
        entry = self.card_entries.get(card)
        if not entry:
            return
        folder = self.output_path
        if not folder:
            self.ui_warn('Folder Error', 'Select a folder first.')
            return
        url = self.entry_url_for_entry(entry)
        if url is None:
            self.log("Transcript failed: entry has no resolvable per-item URL.")
            self.queue_card_status(card, "Transcript failed (No item URL)", state="failed")
            return
        title = entry.get('title', 'Video')
        self.last_action_kind = "transcript"
        self.last_selected_format = None
        self.last_selected_label = None
        self.last_selected_title = title
        self.last_transcript_source = None
        self.last_transcript_languages = []
        self.set_progress(0)
        self.queue_card_busy(card, True)
        self.queue_card_status(card, "Downloading transcript", state="downloading")
        self.queue_card_progress(card, 0)
        if selected_transcript is not None:
            self.log(f"Starting transcript download: {title} [{selected_transcript.label}]")
        else:
            self.log(f"Starting transcript download: {title}")
        threading.Thread(
            target=self.download_transcript,
            args=(url, folder, card, selected_transcript),
            daemon=True,
        ).start()

    def handle_card_whisper(self, card, selected_model: str) -> None:
        """Main-thread decision point: freeze one TranscriptionRequest and start a worker.

        Rejects non-current/busy cards and aliases outside the probed exact list.
        No card/widget/callback/settings objects cross into core.
        """
        if threading.get_ident() != self.main_thread_id:
            self._log_whisper_safely(
                "Ignored Whisper action started off the UI thread."
            )
            return
        if not self._card_in_session(card):
            return
        if card in self.busy_cards:
            self.log("Whisper ignored: card already has an active action.")
            return
        folder = self.output_path
        if not folder:
            self.ui_warn("Folder Error", "Select a folder first.")
            return
        model_name = str(selected_model or "").strip()
        if not model_name or model_name not in self.whisper_models:
            self.log(
                "Whisper ignored: selected model is not in the probed alias list."
            )
            return

        entry = self.card_entries.get(card)
        if not isinstance(entry, dict):
            return
        source_url = self.entry_url_for_entry(entry)
        if source_url is None:
            self.log("Whisper failed: entry has no resolvable per-item URL.")
            self.queue_card_status(card, "Whisper failed (No item URL)", state="failed")
            return
        title = str(entry.get("title") or "Video")
        verified_media = self.verified_media_by_card.get(card)
        request_id = str(uuid.uuid4())
        request = TranscriptionRequest(
            request_id=request_id,
            source_url=source_url,
            title=title,
            output_dir=Path(folder),
            model_name=model_name,
            verified_media=verified_media,
        )

        worker_started = False

        def run_worker() -> None:
            nonlocal worker_started
            # Ownership transfers as soon as a live thread enters its target.
            # This also covers a custom Thread.start() that launches then raises.
            worker_started = True
            self.run_whisper_transcription(card, request)

        try:
            # Main-thread ownership only; workers never mutate these maps.
            self.whisper_request_id_by_card[card] = request_id
            self.latest_whisper_card = card
            self.last_action_kind = "whisper"
            self.last_selected_format = None
            self.last_selected_label = model_name
            self.last_selected_title = title
            self.last_transcript_source = None
            self.last_transcript_languages = []
            self.queue_card_busy(card, True)
            initial_snapshot = WhisperRunSnapshot(
                request_id=request_id,
                phase=WhisperPhase.PREPARING_AUDIO,
                model_name=model_name,
                cache_root=str(model_cache_dir()),
                effective_device=None,
                fallback_used=False,
                source_kind=None,
                error_reason=None,
                updated_at=time.time(),
            )
            self.queue_whisper_snapshot(card, request_id, initial_snapshot)
            self.queue_card_progress_mode(card, request_id, "indeterminate")
            self.queue_whisper_status(
                card,
                request_id,
                _WHISPER_PHASE_STATUS[WhisperPhase.PREPARING_AUDIO],
                "downloading",
            )
            self.log(f"Starting Whisper transcription: {title} [{model_name}]")
            worker = threading.Thread(target=run_worker, daemon=True)
            worker.start()
            # A successful start transfers cleanup even if the target has not
            # reached run_worker yet.
            worker_started = True
        except Exception as exc:
            if not worker_started:
                self._cleanup_failed_whisper_start(card, request, exc)
            else:
                # A live worker owns terminal cleanup. Suppress callback errors
                # from synchronous test/custom thread implementations.
                self._log_whisper_safely(
                    "Whisper worker raised while taking ownership "
                    f"({type(exc).__name__}: {exc})."
                )

    def _cleanup_failed_whisper_start(
        self,
        card,
        request: TranscriptionRequest,
        exc: BaseException,
    ) -> None:
        """Best-effort main-thread rollback before a worker owns cleanup."""
        request_id = request.request_id
        # Another request may have taken ownership while a custom/re-entrant
        # startup helper was running. Stale cleanup must be a complete no-op.
        if self.whisper_request_id_by_card.get(card) != request_id:
            return
        error_reason = self._sanitize_whisper_error(exc)

        try:
            self.log_error("Whisper", exc)
        except Exception:
            self._log_whisper_safely(
                "Whisper failed before its worker started "
                f"({type(exc).__name__}: {error_reason})."
            )
        finally:
            # Generic classification may call this error "unknown"; the
            # actionable Whisper boundary reason remains authoritative.
            self.last_error_reason = error_reason

        try:
            cache_root = str(model_cache_dir())
        except Exception:
            cache_root = ""
        failed_snapshot = WhisperRunSnapshot(
            request_id=request_id,
            phase=WhisperPhase.FAILED,
            model_name=request.model_name,
            cache_root=cache_root,
            effective_device=None,
            fallback_used=False,
            source_kind=None,
            error_reason=error_reason,
            updated_at=time.time(),
        )
        status = self._whisper_phase_status_text(
            WhisperPhase.FAILED,
            error_reason=error_reason,
        )

        self._best_effort_whisper_start_event(
            "whisper_snapshot",
            (card, request_id, failed_snapshot),
            lambda: self.queue_whisper_snapshot(card, request_id, failed_snapshot),
        )
        self._best_effort_whisper_start_event(
            "whisper_status",
            (card, request_id, status, "failed"),
            lambda: self.queue_whisper_status(
                card,
                request_id,
                status,
                "failed",
            ),
        )
        self._best_effort_whisper_start_event(
            "card_progress_mode",
            (card, request_id, "reset"),
            lambda: self.queue_card_progress_mode(card, request_id, "reset"),
        )

        # Direct rollback is intentionally main-thread-only. It cannot depend
        # on another queue/log call succeeding.
        self.busy_cards.discard(card)
        try:
            if self._card_in_session(card):
                card.set_actions_enabled(bool(self.output_path))
        except Exception:
            pass
        # Retain the matching request ID so the terminal FAILED snapshot stays
        # current for Copy Diagnostics. A later action replaces it normally.

    def _best_effort_whisper_start_event(
        self,
        action: str,
        payload,
        publish,
    ) -> None:
        """Publish startup-failure UI state, falling back to the consumer."""
        try:
            publish()
            return
        except Exception:
            pass
        try:
            # This method is used only by the main-thread startup rollback.
            # Snapshot mutation therefore still goes through the sole consumer.
            self._consume_whisper_ui_event(action, payload)
        except Exception:
            pass

    def _sanitize_whisper_error(self, exc: BaseException) -> str:
        """Return a core-sanitized actionable reason for a failed snapshot."""
        if isinstance(exc, CookiesRequiredError):
            value = exc.original or exc
            fallback = (
                "Cookies required; select cookies.txt to retry Whisper audio download"
            )
        elif isinstance(exc, AudioAcquisitionError):
            value = exc
            fallback = "Audio download failed; no usable audio source was produced"
        else:
            value = exc
            fallback = f"{type(exc).__name__}: transcription failed"
        return (
            sanitize_whisper_error_reason(value, fallback=fallback)
            or "Whisper transcription failed"
        )

    def _whisper_phase_status_text(
        self,
        phase: WhisperPhase,
        *,
        result: TranscriptionResult | None = None,
        error_reason: str | None = None,
    ) -> str:
        if phase is WhisperPhase.COMPLETE and result is not None:
            text_name = Path(result.text_path).name
            srt_name = Path(result.srt_path).name
            status = f"Whisper complete: {text_name}, {srt_name}"
            if result.fallback_used:
                status += " (CPU fallback)"
            return status
        if phase is WhisperPhase.FAILED:
            reason = error_reason or "transcription failed"
            return f"Whisper failed: {reason}"
        return _WHISPER_PHASE_STATUS.get(phase, phase.value.replace("_", " ").title())

    def _log_whisper_worker_event_failure(
        self,
        action: str,
        exc: BaseException,
    ) -> None:
        """Best-effort debug logging without calling a failed UI/log helper."""
        try:
            self._append_debug_log(
                "Whisper worker could not publish "
                f"{action} ({type(exc).__name__}: {exc})."
            )
        except Exception:
            pass

    def _queue_whisper_worker_event(
        self,
        action: str,
        payload,
        publish,
    ) -> bool:
        """Publish one worker event, falling back to its raw queue shape.

        This method never applies an event directly. Workers therefore never
        mutate widgets, session maps, request IDs, or busy ownership.
        """
        try:
            publish()
            return True
        except Exception as exc:
            self._log_whisper_worker_event_failure(action, exc)
        try:
            self.ui_queue.put((action, payload))
            return True
        except Exception as exc:
            self._log_whisper_worker_event_failure(action, exc)
            return False

    def _publish_whisper_worker_terminal(
        self,
        card,
        request_id: str,
        snapshot: WhisperRunSnapshot,
        status: str,
        state: str,
    ) -> None:
        """Attempt terminal snapshot and status independently, in that order."""
        self._queue_whisper_worker_event(
            "whisper_snapshot",
            (card, request_id, snapshot),
            lambda: self.queue_whisper_snapshot(card, request_id, snapshot),
        )
        self._queue_whisper_worker_event(
            "whisper_status",
            (card, request_id, status, state),
            lambda: self.queue_whisper_status(
                card,
                request_id,
                status,
                state,
            ),
        )

    def run_whisper_transcription(self, card, request: TranscriptionRequest) -> None:
        """Daemon worker: resolve audio, transcribe, queue UI events.

        Never touches widgets, ``whisper_runs_by_card``, request maps, or
        ``busy_cards`` directly. Reused media stays zero-download. Missing or
        stale media uses Phase 3A ``AudioSourceResolver`` with request-aware
        acquisition progress and one UI-mediated cookies.txt retry.
        """
        request_id: str | None = None
        model_name = ""
        cache_root = ""
        source_kind: str | None = None
        effective_device: str | None = None
        fallback_used = False
        lease = None
        terminal_published = False
        downloading_audio_announced = False
        last_acquisition_progress: float | None = None

        try:
            # All request-derived metadata and cache resolution are inside the
            # lifecycle boundary so any entry failure receives one FAILED
            # terminal and the request-aware finally cleanup.
            request_id = request.request_id
            model_name = request.model_name
            cache_root = str(model_cache_dir())

            def emit_snapshot(
                phase: WhisperPhase,
                *,
                error: str | None = None,
                device: str | None = None,
                fallback: bool | None = None,
                kind: str | None = None,
            ) -> None:
                snapshot = WhisperRunSnapshot(
                    request_id=request_id,
                    phase=phase,
                    model_name=model_name,
                    cache_root=cache_root,
                    effective_device=effective_device if device is None else device,
                    fallback_used=fallback_used if fallback is None else fallback,
                    source_kind=source_kind if kind is None else kind,
                    error_reason=error,
                    updated_at=time.time(),
                )
                self.queue_whisper_snapshot(card, request_id, snapshot)

            def on_phase(phase: WhisperPhase) -> None:
                nonlocal fallback_used, downloading_audio_announced
                # The worker publishes canonical result/error terminals after
                # service return/raise. Callback terminals are never forwarded.
                if phase in (WhisperPhase.COMPLETE, WhisperPhase.FAILED):
                    return
                if phase is WhisperPhase.FALLING_BACK_CPU:
                    fallback_used = True
                    self._log_whisper_safely(
                        "Whisper GPU unavailable; falling back to CPU "
                        f"for {model_name}."
                    )
                if phase in _WHISPER_INDETERMINATE_PHASES:
                    self.queue_card_progress_mode(
                        card,
                        request_id,
                        "indeterminate",
                    )
                elif phase is WhisperPhase.DOWNLOADING_AUDIO:
                    downloading_audio_announced = True
                    self.queue_card_progress_mode(
                        card,
                        request_id,
                        "determinate",
                    )
                status = self._whisper_phase_status_text(phase)
                self.queue_whisper_status(
                    card,
                    request_id,
                    status,
                    "downloading",
                )
                emit_snapshot(phase, fallback=fallback_used)

            def publish_acquisition_progress(progress: float) -> None:
                """Publish strictly forward real acquisition progress only."""
                nonlocal downloading_audio_announced, last_acquisition_progress
                progress = max(0.0, min(1.0, progress))
                # yt-dlp estimates can move backward as the total is refined.
                # Keep the card monotonic and suppress duplicate updates.
                if (
                    last_acquisition_progress is not None
                    and progress <= last_acquisition_progress
                ):
                    return
                if not downloading_audio_announced:
                    on_phase(WhisperPhase.DOWNLOADING_AUDIO)
                last_acquisition_progress = progress
                self.queue_whisper_card_progress(card, request_id, progress)

            def acquisition_progress_hook(d) -> None:
                if not isinstance(d, dict):
                    return
                status = d.get("status")
                if status == "downloading":
                    downloaded = d.get("downloaded_bytes")
                    if downloaded is None:
                        return
                    try:
                        downloaded_value = float(downloaded)
                    except (TypeError, ValueError):
                        return
                    if not math.isfinite(downloaded_value) or downloaded_value < 0:
                        return

                    total_value = None
                    for total in (
                        d.get("total_bytes"),
                        d.get("total_bytes_estimate"),
                    ):
                        try:
                            candidate = float(total)
                        except (TypeError, ValueError):
                            continue
                        if math.isfinite(candidate) and candidate > 0:
                            total_value = candidate
                            break
                    if total_value is None:
                        return
                    publish_acquisition_progress(downloaded_value / total_value)
                elif (
                    status == "finished"
                    and last_acquisition_progress is not None
                ):
                    # Resolver/post-hook validation owns output-path truth. A
                    # finished notification can only complete a bar already
                    # grounded by this attempt's real byte counters.
                    publish_acquisition_progress(1.0)

            def resolve_audio_source():
                return self.audio_source_resolver.resolve(
                    request,
                    progress_hook=acquisition_progress_hook,
                )

            def prompt_cookies_for_whisper_retry(cookie_exc: CookiesRequiredError) -> None:
                """Main-thread cookies.txt prompt; worker never touches Tk."""
                try:
                    self.log_error("Whisper", cookie_exc.original or cookie_exc)
                except Exception as log_exc:
                    self._log_whisper_worker_event_failure("cookie log", log_exc)
                confirmed = False
                try:
                    confirmed = bool(
                        self.ui_confirm(
                            "Cookies required",
                            "This Whisper audio download may require cookies "
                            "(age/consent/bot checks).\n"
                            "Automatic browser-cookie retry did not finish "
                            "successfully.\n\n"
                            "Select a cookies.txt file to retry?",
                        )
                    )
                except Exception as prompt_exc:
                    self._log_whisper_worker_event_failure(
                        "cookie prompt",
                        prompt_exc,
                    )
                    raise AudioAcquisitionError(
                        "Cookies prompt failed; Whisper audio download was not retried"
                    ) from prompt_exc
                if not confirmed:
                    self._log_whisper_safely("Cookies selection declined.")
                    raise AudioAcquisitionError(
                        "Cookies selection declined; Whisper audio download was not retried"
                    )
                try:
                    self.ui_select_cookies()
                except Exception as select_exc:
                    self._log_whisper_worker_event_failure(
                        "cookie select",
                        select_exc,
                    )
                    raise AudioAcquisitionError(
                        "Cookies selection failed; Whisper audio download was not retried"
                    ) from select_exc
                if not self.cookies_path:
                    self._log_whisper_safely("No cookies.txt selected.")
                    raise AudioAcquisitionError(
                        "No cookies.txt selected; Whisper audio download was not retried"
                    )

            on_phase(WhisperPhase.PREPARING_AUDIO)
            try:
                lease = resolve_audio_source()
            except CookiesRequiredError as cookie_exc:
                # Phase 3A already removed the first-attempt workspace before
                # raising. Prompt once on the UI thread, then retry with a
                # fresh workspace and the same immutable request.
                with self._whisper_cookie_retry_lock:
                    prompt_cookies_for_whisper_retry(cookie_exc)
                    downloading_audio_announced = False
                    last_acquisition_progress = None
                    on_phase(WhisperPhase.PREPARING_AUDIO)
                    try:
                        lease = resolve_audio_source()
                    except CookiesRequiredError as retry_exc:
                        try:
                            self.log_error(
                                "Whisper",
                                retry_exc.original or retry_exc,
                            )
                        except Exception as log_exc:
                            self._log_whisper_worker_event_failure(
                                "cookie retry log",
                                log_exc,
                            )
                        raise AudioAcquisitionError(
                            "Cookies still required after cookies.txt retry; "
                            "Whisper audio download failed"
                        ) from retry_exc

            source_kind = (
                lease.kind.value
                if isinstance(lease.kind, SourceKind)
                else str(lease.kind)
            )
            emit_snapshot(WhisperPhase.PREPARING_AUDIO, kind=source_kind)

            with lease:
                result = self.whisper_service.transcribe(
                    request,
                    lease.path,
                    on_phase=on_phase,
                )
                effective_device = result.effective_device
                fallback_used = bool(result.fallback_used)

                # Finish fallible nonterminal work before publishing COMPLETE.
                # Its failure is diagnostic only because verified result files
                # already exist and must not be relabeled FAILED.
                completion_log = (
                    f"Whisper complete: {Path(result.text_path).name}, "
                    f"{Path(result.srt_path).name}"
                    + (" (CPU fallback)" if result.fallback_used else "")
                )
                try:
                    self.log(completion_log)
                except Exception as exc:
                    self._log_whisper_worker_event_failure("completion log", exc)

                status = self._whisper_phase_status_text(
                    WhisperPhase.COMPLETE,
                    result=result,
                )
                complete_snapshot = WhisperRunSnapshot(
                    request_id=request_id,
                    phase=WhisperPhase.COMPLETE,
                    model_name=model_name,
                    cache_root=cache_root,
                    effective_device=effective_device,
                    fallback_used=fallback_used,
                    source_kind=source_kind,
                    error_reason=None,
                    updated_at=time.time(),
                )
                # Mark terminal ownership before helper calls. Any helper or
                # fallback failure remains best-effort and cannot trigger FAILED.
                terminal_published = True
                self._publish_whisper_worker_terminal(
                    card,
                    request_id,
                    complete_snapshot,
                    status,
                    "complete",
                )
        except Exception as exc:
            if not terminal_published and request_id is not None:
                error_reason = self._sanitize_whisper_error(exc)
                # Preserve useful context in logs; status/snapshot stay sanitized.
                try:
                    self.log_error("Whisper", exc)
                except Exception as log_exc:
                    self._log_whisper_worker_event_failure(
                        "failure log",
                        log_exc,
                    )
                finally:
                    # Generic classification is for legacy download diagnostics;
                    # the sanitized Whisper boundary reason is authoritative here.
                    self.last_error_reason = error_reason
                status = self._whisper_phase_status_text(
                    WhisperPhase.FAILED,
                    error_reason=error_reason,
                )
                failed_snapshot = WhisperRunSnapshot(
                    request_id=request_id,
                    phase=WhisperPhase.FAILED,
                    model_name=model_name,
                    cache_root=cache_root,
                    effective_device=effective_device,
                    fallback_used=fallback_used,
                    source_kind=source_kind,
                    error_reason=error_reason,
                    updated_at=time.time(),
                )
                terminal_published = True
                self._publish_whisper_worker_terminal(
                    card,
                    request_id,
                    failed_snapshot,
                    status,
                    "failed",
                )
            elif terminal_published:
                self._log_whisper_worker_event_failure(
                    "post-terminal work",
                    exc,
                )
        finally:
            # Always reset progress and clear busy through the main-thread queue.
            # Request-aware busy clear: stale workers cannot re-enable a newer action.
            if request_id is not None:
                self._queue_whisper_worker_event(
                    "card_progress_mode",
                    (card, request_id, "reset"),
                    lambda: self.queue_card_progress_mode(
                        card,
                        request_id,
                        "reset",
                    ),
                )
                self._queue_whisper_worker_event(
                    "whisper_busy",
                    (card, request_id, False),
                    lambda: self.queue_whisper_busy(
                        card,
                        request_id,
                        False,
                    ),
                )
            try:
                if lease is not None:
                    lease.release()
            except Exception:
                pass

    def _download_direct_media(
        self,
        card,
        media_url: str,
        headers: dict,
        title: str,
        *,
        media_epoch: int | None = None,
    ) -> None:
        folder = self.output_path
        if not folder:
            self.ui_warn('Folder Error', 'Select a folder first.')
            return
        if media_epoch is None:
            media_epoch = self.invalidate_verified_media(card)
        safe_title = sanitize_filename(title)
        outfile = unique_output_path(folder, safe_title, "mp4")
        self.last_action_kind = "media"
        self.last_selected_format = "ffmpeg"
        self.last_selected_label = "Direct HLS"
        self.last_selected_title = title
        self.last_transcript_source = None
        self.last_transcript_languages = []
        self.set_progress(0)
        self.queue_card_busy(card, True)
        self.queue_card_status(card, "Downloading (Direct HLS)", state="downloading")
        self.queue_card_progress(card, 0)
        self.log(f"Starting ffmpeg download: {title}")

        def run_ffmpeg():
            last_logged_pct = [-10]

            def on_progress(fraction, elapsed, duration):
                self._ffmpeg_progress_hook(
                    fraction,
                    elapsed,
                    duration,
                    last_logged_pct=last_logged_pct,
                )
                self.queue_card_progress(card, fraction)

            try:
                download_with_ffmpeg(
                    media_url,
                    outfile,
                    headers or {},
                    progress_hook=on_progress,
                )
                verified_files = self._verify_download_outputs([outfile])
                if len(verified_files) == 1:
                    verified = verified_files[0]
                    self.set_progress(1)
                    self.queue_card_progress(card, 1)
                    self.queue_card_status(card, "Complete", state="complete")
                    self.log(
                        f"ffmpeg download complete: {verified.path} "
                        f"({verified.size:,} bytes)"
                    )
                    self.queue_verified_media(card, verified, media_epoch)
                else:
                    self.set_progress(0)
                    self.queue_card_status(card, "Failed (Direct HLS)", state="failed")
                    self.log(
                        "Download verification failed: ffmpeg reported success, "
                        "but Link2Vid could not confirm the final file exists."
                    )
                    self.log(f"Unverified output path: {outfile}")
            except Exception as exc:
                self.set_progress(0)
                self.queue_card_status(card, "Failed (Direct HLS)", state="failed")
                self.log_error("ffmpeg", exc)
            finally:
                self.queue_card_busy(card, False)

        threading.Thread(target=run_ffmpeg, daemon=True).start()

    def download_video(self, url, format_id, out_path, card=None, fmt_label=None, media_epoch=None):
        finished_logged = False
        saved_files = []
        ok = False

        def hook(d):
            nonlocal finished_logged
            if d['status'] == 'downloading':
                tot = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                progress = d.get('downloaded_bytes', 0) / tot
                self.set_progress(progress)
                if card:
                    self.queue_card_progress(card, progress)
            elif d['status'] == 'finished' and not finished_logged:
                finished_logged = True
                self.set_progress(1)
                self.log('Download streams complete; finalizing output...')
                if card:
                    self.queue_card_status(card, "Finalizing", state="downloading")

        def post_hook(filepath):
            if filepath:
                final_path = os.path.abspath(filepath)
                saved_files.append(final_path)
                self.log(f"yt-dlp reported final path: {final_path}")

        outtmpl = os.path.join(out_path, '%(title)s.%(ext)s')
        started_at = time.time()
        try:
            self.log(
                "Download runtime: "
                f"pid={os.getpid()} exe={sys.executable} cwd={os.getcwd()} "
                f"out_path={out_path} outtmpl={outtmpl}"
            )
            self.log(f"Output folder before download: {self._folder_snapshot(out_path)}")
            try:
                ok = self.download_manager.download(url, format_id, outtmpl, progress_hook=hook, post_hook=post_hook)
            except CookiesRequiredError as exc:
                self.log_error("Download", exc.original or exc)
                if self.confirm_cookies_retry("video"):
                    self.ui_select_cookies()
                    if not self.cookies_path:
                        self.log("No cookies.txt selected.")
                        ok = False
                    else:
                        try:
                            ok = self.download_manager.download(url, format_id, outtmpl, progress_hook=hook, post_hook=post_hook)
                        except CookiesRequiredError as retry_exc:
                            self.log_error("Download", retry_exc.original or retry_exc)
                            ok = False
                else:
                    self.log("Cookies selection declined.")
                    ok = False
            if not ok and self.last_error_reason == "format unavailable" and format_id != "best":
                if self.confirm_format_fallback():
                    fallback_id = "best"
                    fallback_label = self.format_label_map.get(fallback_id, "Best (single file)")
                    self.log(f"Retrying download with {fallback_label} due to unavailable format.")
                    self.last_selected_format = fallback_id
                    self.last_selected_label = fallback_label
                    if card:
                        self.set_progress(0)
                        self.queue_card_progress(card, 0)
                        self.queue_card_status(card, f"Retrying ({fallback_label})", state="downloading")
                    try:
                        ok = self.download_manager.download(url, fallback_id, outtmpl, progress_hook=hook, post_hook=post_hook)
                    except CookiesRequiredError as retry_exc:
                        self.log_error("Download", retry_exc.original or retry_exc)
                        ok = False
                    fmt_label = fallback_label
            if ok:
                has_reported_outputs = bool(saved_files)
                output_files = saved_files or self._recent_download_outputs(out_path, started_at)
                verified_files = self._verify_download_outputs(output_files)
                self.log(f"Output folder after download: {self._folder_snapshot(out_path)}")
                if verified_files:
                    all_preexisting = True
                    for output in verified_files:
                        was_preexisting = output.modified_at < started_at - 1
                        all_preexisting = all_preexisting and was_preexisting
                        status = "already existed" if was_preexisting else "saved"
                        self.log(f"Download {status}: {output.path} ({output.size:,} bytes)")
                    self.set_progress(1)
                    if card:
                        self.queue_card_progress(card, 1)
                        status = "Already downloaded" if all_preexisting else "Complete"
                        self.queue_card_status(card, status, state="complete")
                        # Folder recency fallback is legacy UX evidence only. Whisper
                        # provenance requires one exact path reported by this action.
                        if (
                            media_epoch is not None
                            and has_reported_outputs
                            and len(verified_files) == 1
                        ):
                            self.queue_verified_media(card, verified_files[0], media_epoch)
                else:
                    ok = False
                    self.log(
                        "Download verification failed: yt-dlp returned success, "
                        "but Link2Vid could not confirm the final file exists."
                    )
                    if output_files:
                        for saved_file in output_files:
                            self.log(f"Unverified output path: {saved_file}")
                    else:
                        self.log("yt-dlp did not report a final output path.")
                    self.log(f"Output folder snapshot: {self._folder_snapshot(out_path)}")
            if not ok and card:
                label = fmt_label or format_id
                self.queue_card_status(card, f"Failed ({label})", state="failed")
        finally:
            if card:
                self.queue_card_busy(card, False)
        return bool(ok)

    def _recent_download_outputs(self, folder: str, started_at: float) -> list[str]:
        ignored_suffixes = (
            ".part",
            ".ytdl",
            ".temp",
            ".tmp",
            ".f136.mp4",
            ".f251.webm",
        )
        try:
            candidates = []
            for entry in Path(folder).iterdir():
                if not entry.is_file():
                    continue
                name = entry.name.lower()
                if any(name.endswith(suffix) for suffix in ignored_suffixes):
                    continue
                stat = entry.stat()
                if stat.st_mtime >= started_at - 1:
                    candidates.append((stat.st_mtime, str(entry)))
            return [path for _mtime, path in sorted(candidates)]
        except Exception:
            return []

    def _verify_download_outputs(self, paths: list[str]) -> list[VerifiedMedia]:
        verified = []
        seen = set()
        for path in paths:
            if not path or path in seen:
                continue
            seen.add(path)
            try:
                file_path = Path(path)
                exists = file_path.exists()
                is_file = file_path.is_file()
                parent = file_path.parent
                parent_exists = parent.exists()
                resolved = file_path.resolve(strict=False)
                stat = file_path.stat() if is_file else None
                size = stat.st_size if stat else None
                self.log(
                    "Verify output: "
                    f"path={file_path} resolved={resolved} exists={exists} "
                    f"is_file={is_file} size={size if size is not None else 'n/a'} "
                    f"parent_exists={parent_exists}"
                )
                if is_file and size and size > 0:
                    verified.append(
                        VerifiedMedia(
                            path=file_path,
                            size=size,
                            modified_at=stat.st_mtime,
                        )
                    )
            except Exception as exc:
                self.log(f"Verify output error for {path}: {type(exc).__name__}: {exc}")
                continue
        return verified

    def _folder_snapshot(self, folder: str, limit: int = 10) -> str:
        try:
            entries = []
            for entry in Path(folder).iterdir():
                if not entry.is_file():
                    continue
                stat = entry.stat()
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
                entries.append((stat.st_mtime, f"{entry.name} ({stat.st_size:,} bytes, {timestamp})"))
            if not entries:
                return "no files"
            newest = [description for _mtime, description in sorted(entries, reverse=True)[:limit]]
            return "; ".join(newest)
        except Exception as exc:
            return f"unavailable ({type(exc).__name__}: {exc})"

    def download_transcript(self, url, out_path, card=None, selected_transcript=None):
        outtmpl = os.path.join(out_path, '%(title)s.transcript.%(ext)s')
        success = False

        def mark_success(result):
            nonlocal success
            success = True
            self.last_transcript_source = result.source
            self.last_transcript_languages = list(result.languages)
            self.set_progress(1)
            if card:
                self.queue_card_progress(card, 1)
                self.queue_card_status(card, f"Transcript saved ({result.source})", state="complete")
            languages = ", ".join(result.languages[:5])
            if len(result.languages) > 5:
                languages += ", ..."
            detail = f" [{languages}]" if languages else ""
            self.log(f"Transcript download complete ({result.source}){detail}")

        try:
            result = self.download_manager.download_transcript(url, outtmpl, selected_track=selected_transcript)
            mark_success(result)
        except CookiesRequiredError as exc:
            self.log_error("Transcript", exc.original or exc)
            if self.confirm_cookies_retry("transcript"):
                self.ui_select_cookies()
                if not self.cookies_path:
                    self.log("No cookies.txt selected.")
                    if card:
                        self.queue_card_status(card, "Transcript failed", state="failed")
                else:
                    try:
                        result = self.download_manager.download_transcript(
                            url,
                            outtmpl,
                            selected_track=selected_transcript,
                        )
                        mark_success(result)
                    except CookiesRequiredError as retry_exc:
                        self.log_error("Transcript", retry_exc.original or retry_exc)
                        if card:
                            self.queue_card_status(card, "Transcript failed", state="failed")
                    except NoTranscriptAvailableError as retry_exc:
                        self.log_error("Transcript", retry_exc)
                        if card:
                            self.queue_card_status(card, "Transcript unavailable", state="failed")
                    except Exception as retry_exc:
                        self.log_error("Transcript", retry_exc)
                        if card:
                            self.queue_card_status(card, "Transcript failed", state="failed")
            else:
                self.log("Cookies selection declined.")
                if card:
                    self.queue_card_status(card, "Transcript failed", state="failed")
        except NoTranscriptAvailableError as exc:
            self.log_error("Transcript", exc)
            if card:
                self.queue_card_status(card, "Transcript unavailable", state="failed")
        except Exception as exc:
            self.log_error("Transcript", exc)
            if card:
                self.queue_card_status(card, "Transcript failed", state="failed")
        finally:
            if card:
                self.queue_card_busy(card, False)
        return success

    def on_close(self):
        try:
            self.thumbnail_loader.clear_cache(remove_dir=False)
        finally:
            self.root.destroy()

    # ──────────────────────────────────────────────────────────
    # Auth helpers / Selenium fallback
    # ──────────────────────────────────────────────────────────
    def prompt_for_credentials(self, url: str):
        username, password = dev_credentials_for_url(url, self.dev_defaults)
        if username and password:
            domain = dev_domain_for_url(url, self.dev_defaults)
            self.log(f"[dev] Using developer.json credentials for {domain}.")
            return username, password

        return prompt_credentials(self.root, url)

    def selenium_fallback(self, page_url, username=None, password=None):
        if not username or not password:
            self.log("Selenium fallback skipped: no credentials provided.")
            return []
        login_plan = resolve_login_plan(page_url, self.dev_defaults)
        entries = selenium_fetch_media_entries(page_url, username, password, login_plan=login_plan, log=self.log)
        if entries:
            return entries
        self.log("Selenium fallback error: failed to find media URLs after login")
        return []

    # ──────────────────────────────────────────────────────────
    # Config load
    # ──────────────────────────────────────────────────────────
    def load_dev_defaults(self):
        from ..core.runtime import resolve_developer_json

        path = resolve_developer_json()
        if path is None:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


__all__ = ["VideoDownloaderApp"]
