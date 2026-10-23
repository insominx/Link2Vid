"""Toolkit-neutral application controller.

Owns fetch/download/transcript/Whisper orchestration. A single dispatcher
thread (``main_thread_id``) is the only mutator of session maps and view
models; workers hop onto it through ``ui_queue``. The frontend talks to the
controller through ``post`` and renders the events its view models emit.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
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

from ..core.cookie_prefs import (
    COOKIE_BROWSERS,
    COOKIE_BROWSER_LABELS,
    cookies_browser_label,
    load_preferred_cookies_browser,
    load_preferred_cookies_path,
    normalize_cookies_browser,
    store_preferred_cookies_browser,
    store_preferred_cookies_path,
)
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
    NeedsLogin,
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
    is_audio_extract_format,
    normalize_url,
    sanitize_filename,
    sanitize_whisper_error_reason,
    unique_output_path,
    selenium_fetch_media_entries,
    url_from_clipboard_text,
)
from ..core.extractors import build_media_entries, title_from_page_url
from ..core.runtime import model_cache_dir
from .view import NullView, ViewPort
from .viewmodels import BatchBarModel, CardModel, FooterModel, LogModel, UrlModel, log_level

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


@dataclass
class BatchPromptPolicy:
    lock: threading.Lock = field(default_factory=threading.Lock)
    cookies_browser_asked: bool = False
    cookies_browser: str | None = None
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


def site_label(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlparse(url).hostname or "").strip()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _site_name(entry: dict) -> str | None:
    site = entry.get("extractor_key") or entry.get("extractor")
    if site and str(site).lower() not in ("generic", "html5mediaembed"):
        return str(site).replace("_", " ").title().replace("Youtube", "YouTube")
    url = entry.get("webpage_url") or entry.get("url") or ""
    host = urlparse(str(url)).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _http_url(value) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def entry_details(entry: dict) -> dict:
    """JSON-safe display fields for one fetched entry."""
    uploader = (
        entry.get("uploader")
        or entry.get("channel")
        or entry.get("uploader_id")
        or entry.get("channel_id")
    )
    thumbnail = entry.get("thumbnail") or entry.get("thumbnail_url")
    thumbnails = entry.get("thumbnails")
    if not thumbnail and isinstance(thumbnails, list) and thumbnails:
        last = thumbnails[-1]
        if isinstance(last, dict):
            thumbnail = last.get("url")
    return {
        "site": _site_name(entry),
        "duration": format_duration_text(entry.get("duration")),
        "uploader": str(uploader) if uploader else None,
        "thumbnail": _http_url(thumbnail),
        "webpage_url": _http_url(entry.get("webpage_url") or entry.get("original_url")),
        "direct": bool(entry.get("_ffmpeg_headers")),
    }


class AppController:
    def __init__(
        self,
        view: ViewPort | None = None,
        *,
        whisper_service: WhisperService | None = None,
        audio_source_resolver: AudioSourceResolver | None = None,
    ):
        self.view = view if view is not None else NullView()
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
        self.cookies_browser = load_preferred_cookies_browser(dev_defaults=self.dev_defaults)
        self.user_settings_path = None
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
        self.batch_size = 40
        self.rendered_count = 0
        self.busy_cards = set()
        # Dispatcher-thread session map: exact verified media for Whisper reuse.
        # Only process_ui_queue / dispatcher helpers mutate this; workers enqueue events.
        self.verified_media_by_card: dict = {}
        self._verified_media_epoch_by_card: dict = {}
        # Whisper session state. Maps mutate only via the UI queue consumer.
        self.whisper_runs_by_card: dict = {}
        self.whisper_request_id_by_card: dict = {}
        self.latest_whisper_card = None
        self.whisper_models: list[str] = []
        # Serialize the interactive selection plus immediate retry so two
        # Whisper workers cannot apply each other's app-global cookies path.
        self._whisper_cookie_retry_lock = threading.Lock()
        self._stopped = threading.Event()
        self.output_path = self.get_default_output_path()
        self.cookies_path = load_preferred_cookies_path(dev_defaults=self.dev_defaults)
        if self.dev_defaults.get('cookies_path'):
            self.cookies_path = self.dev_defaults.get('cookies_path')
        self.ydl_logger = YtDlpLogger(self)
        self.download_manager = DownloadManager(
            ydl_logger=self.ydl_logger,
            log=self.log,
            log_error=self.log_error,
            dev_defaults=self.dev_defaults,
            get_cookies_path=lambda: self.cookies_path,
            get_cookies_browser=lambda: self.cookies_browser,
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

        emit = self.view.emit
        self.url_entry = UrlModel(emit)
        self.batch_bar = BatchBarModel(emit)
        self.footer = FooterModel(
            output_path=self.output_path,
            cookies_browser=self.cookies_browser,
            cookies_path=self.cookies_path,
            emit=emit,
        )
        self.log_drawer = LogModel(emit)

        from ..core.runtime import startup_summary

        self.log(startup_summary())
        self.log(
            "UI runtime: "
            f"pid={os.getpid()} exe={sys.executable} cwd={os.getcwd()} "
            f"source={os.path.abspath(__file__)} output={self.output_path} "
            f"debug_log={self.debug_log_path}"
        )

        if self.dev_defaults.get('use_defaults') and self.dev_defaults.get('default_url'):
            self.url_entry.set(self.dev_defaults['default_url'])
        else:
            self._populate_url_from_clipboard_if_valid()

        self.update_button_states()
        self.log(f"yt-dlp version: {get_yt_dlp_version()}")
        self.check_ffmpeg()
        self.check_js_runtime()
        self._probe_whisper_models()
        self.log(
            "Tip: Doggybag reads cookies from the browser chosen in the status bar. "
            f"Current source: {cookies_browser_label(self.cookies_browser)}."
        )

    def boot_state(self) -> dict:
        """Everything the frontend needs to render before the first event."""
        return {
            "url": self.url_entry.get(),
            "formats": [
                {"id": opt["format"], "label": opt["label"]} for opt in self.format_options
            ],
            "whisper_models": list(self.whisper_models),
            "cookie_browsers": [
                {"id": name, "label": COOKIE_BROWSER_LABELS[name]} for name in COOKIE_BROWSERS
            ],
            "settings": self.footer.state(),
            "batch": self.batch_bar.state(),
            "fetching": self.is_fetching,
            "health": self.health_state(),
            "log": [
                {"time": "", "level": log_level(line), "text": line}
                for line in self.log_history[-self.log_max:]
            ],
            "debug_log_path": self.debug_log_path,
        }

    def health_state(self) -> dict:
        return {
            "yt_dlp": get_yt_dlp_version(),
            "ffmpeg": bool(getattr(self, "ffmpeg_available", False)),
            "js_runtimes": list(getattr(self, "js_runtimes", []) or []),
            "whisper_models": len(self.whisper_models),
        }

    def _populate_url_from_clipboard_if_valid(self) -> None:
        url = self.clipboard_url()
        if url:
            self.url_entry.set(url)

    def clipboard_url(self) -> str | None:
        try:
            clip = self.view.get_clipboard()
        except Exception:
            return None
        return url_from_clipboard_text(clip) if clip else None

    # ──────────────────────────────────────────────────────────
    # Dispatcher thread
    # ──────────────────────────────────────────────────────────
    def post(self, func) -> None:
        """Run ``func`` on the dispatcher thread without waiting."""
        self.ui_queue.put(("call", func))

    def run_forever(self) -> None:
        """Become the dispatcher thread and drain events until ``stop``."""
        self.main_thread_id = threading.get_ident()
        while not self._stopped.is_set():
            self.process_ui_queue(timeout=0.1)

    def stop(self) -> None:
        self._stopped.set()

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
        if getattr(self, "log_drawer", None) is not None:
            self.log_drawer.append(message)

    def _default_debug_log_path(self) -> str:
        downloads = Path.home() / "Downloads"
        parent = downloads if downloads.exists() else Path(tempfile.gettempdir())
        return str(parent / "doggybag-debug.log")

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

    def _ask(self, kind: str, payload: dict):
        """Open a frontend dialog and block the caller until it is answered.

        On the dispatcher thread the queue keeps draining while the dialog is
        open (like a modal event loop) so progress and logs stay live.
        """
        future: Future = self.view.request(kind, payload)
        if threading.get_ident() == self.main_thread_id:
            while not future.done():
                self.process_ui_queue(timeout=0.05)
        return future.result()

    def ui_confirm(self, title: str, message: str) -> bool:
        return bool(
            self._ask(
                "confirm",
                {"title": title, "message": message, "confirm": "Continue", "cancel": "Not now"},
            )
        )

    def ui_ffmpeg_fallback(self, fmt_label: str) -> str:
        answer = self._ask(
            "choice",
            {
                "title": "ffmpeg is missing",
                "message": (
                    f"Merging audio and video for '{fmt_label}' needs ffmpeg, "
                    "which Doggybag could not find."
                ),
                "options": [
                    {
                        "id": "fallback",
                        "label": "Use best single file",
                        "detail": "Most compatible; may be lower quality",
                        "primary": True,
                    },
                    {
                        "id": "continue",
                        "label": "Continue anyway",
                        "detail": "May fail or save video without audio",
                    },
                ],
            },
        )
        return answer if answer in ("fallback", "continue") else "cancel"

    def _suggested_cookies_browser(self) -> str | None:
        if self.cookies_browser is not None:
            return self.cookies_browser
        try:
            detected = self.download_manager._running_browsers()
        except Exception:
            detected = []
        if isinstance(detected, (list, tuple)):
            running = [item for item in detected if isinstance(item, str)]
            if running:
                return running[0]
        return None

    def ui_choose_cookies_browser(self) -> str | None:
        answer = self._ask(
            "cookie_browser",
            {
                "title": "This site wants you signed in",
                "message": (
                    "Doggybag can borrow your login cookies from a browser. "
                    "Pick the one you use for this site; Doggybag remembers it. "
                    "Close that browser first if it is running."
                ),
                "suggested": self._suggested_cookies_browser(),
            },
        )
        return normalize_cookies_browser(answer) if answer else None

    def choose_cookies_browser(self) -> str | None:
        policy = self.batch_prompt_policy
        if policy is None:
            return self.ui_choose_cookies_browser()
        with policy.lock:
            if not policy.cookies_browser_asked:
                policy.cookies_browser_asked = True
                policy.cookies_browser = self.ui_choose_cookies_browser()
            return policy.cookies_browser

    def set_cookies_browser(self, browser: str | None, *, persist: bool = True) -> None:
        self.cookies_browser = normalize_cookies_browser(browser)
        # Picking a browser here is a deliberate act, so honour it on the very next
        # attempt. The stored preference alone stays opt-in: on a fresh launch
        # downloads start cookieless, which is what public videos actually need.
        manager = getattr(self, "download_manager", None)
        if manager is not None:
            manager.force_browser_cookies = bool(self.cookies_browser)
        if persist:
            try:
                store_preferred_cookies_browser(
                    self.cookies_browser,
                    path=getattr(self, "user_settings_path", None),
                )
            except Exception as exc:
                self.log(f"Could not save cookie browser preference: {exc}")
        footer = getattr(self, "footer", None)
        if footer is not None:
            setter = getattr(footer, "set_cookies_browser", None)
            if callable(setter):
                setter(self.cookies_browser)
        self.log(f"Cookie source set to {cookies_browser_label(self.cookies_browser)}.")

    def _prepare_cookie_retry(self) -> bool:
        """Ask for a browser only when none is saved. Return True to retry."""
        if self.cookies_browser:
            self.log(
                f"Could not read cookies from {cookies_browser_label(self.cookies_browser)}. "
                "Close that browser (including background processes) and retry, "
                "or change Cookies in the status bar."
            )
            return False
        chosen = self.choose_cookies_browser()
        if not chosen:
            self.log("Browser cookie source not selected.")
            return False
        self.set_cookies_browser(chosen)
        return True

    def confirm_format_fallback(self, *, extract_audio: bool = False) -> bool:
        if extract_audio:
            message = (
                "The selected audio format is not available for this video.\n"
                "Retry by extracting MP3 from the best available file?"
            )
        else:
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
        """Non-blocking notice; the frontend shows it as a toast."""
        self.view.notify("warning", title, message)

    def ui_error_guidance(self, title: str, message: str) -> bool:
        """Explain a known failure. Returns True when the user wants diagnostics."""
        return bool(
            self._ask(
                "guidance",
                {
                    "title": title,
                    "message": message,
                    "confirm": "Copy diagnostics",
                    "cancel": "Close",
                },
            )
        )

    def ui_select_cookies(self) -> None:
        """Compatibility seam: choose a browser instead of a cookies.txt file."""
        chosen = self.choose_cookies_browser()
        if chosen:
            self.set_cookies_browser(chosen)

    def ui_prompt_for_credentials(self, url: str, *, browser_automation: bool = True):
        return self.prompt_for_credentials(url, browser_automation=browser_automation)

    def process_ui_queue(self, timeout: float | None = None):
        """Drain queued events on the dispatcher thread.

        With ``timeout`` the first read blocks up to that long, so a loop can
        idle without spinning. Without it, drains whatever is queued now.
        """
        block = timeout is not None
        while True:
            try:
                if block:
                    action, payload = self.ui_queue.get(timeout=timeout)
                    block = False
                else:
                    action, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._apply_ui_event(action, payload)
            except Exception as exc:
                self._log_whisper_safely(
                    f"Dropped {action} event ({type(exc).__name__}: {exc})."
                )

    def _apply_ui_event(self, action, payload) -> None:
        if action == "call":
            payload()
        elif action == "log":
            self._append_log(payload)
        elif action == "progress":
            self.set_progress(payload)
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
        elif action == "card_outputs":
            card, paths = payload
            if self._card_in_session(card):
                card.set_outputs(paths)
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
            "whisper_outputs",
        ):
            self._consume_whisper_ui_event(action, payload)
        elif action == "fetch_done":
            self.is_fetching = False
            self.update_button_states()

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
            elif action == "whisper_outputs":
                self._apply_whisper_outputs_event(payload)
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
            if not card.is_alive():
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

    def _apply_whisper_outputs_event(self, payload) -> None:
        """Attach a finished run's transcript files to its card (request-aware)."""
        try:
            card, request_id, paths = payload
        except (TypeError, ValueError):
            return
        if not self._whisper_event_is_current(card, request_id):
            return
        setter = getattr(card, "set_outputs", None)
        if callable(setter):
            setter([str(path) for path in paths])

    def queue_whisper_outputs(self, card, request_id: str, paths) -> None:
        payload = (card, request_id, tuple(paths))
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("whisper_outputs", payload))
            return
        self._consume_whisper_ui_event("whisper_outputs", payload)

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
            return bool(card.is_alive())
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
        self.view.emit("progress", {"value": round(value, 4)})

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

    def queue_card_outputs(self, card, paths) -> None:
        """Attach saved files to a card so the UI can open or reveal them."""
        if card is None or not callable(getattr(card, "set_outputs", None)):
            return
        payload = (card, tuple(str(path) for path in paths))
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("card_outputs", payload))
            return
        card.set_outputs(payload[1])

    def _set_card_busy(self, card, busy: bool):
        if not card or not card.is_alive():
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
        if self.ui_error_guidance(title, message):
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
            if not card.is_alive():
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
        copied = self.view.set_clipboard("\n".join(lines))
        if copied is False:
            self.log("Could not copy diagnostics to the clipboard.")
            self.view.notify("error", "Could not copy diagnostics")
            return
        self.log("Diagnostics copied to clipboard.")
        self.view.notify("success", "Diagnostics copied", "Paste them into a bug report or chat.")

    def update_button_states(self, event=None):
        url = self.url_entry.get().strip()
        has_url = bool(url)
        has_folder = bool(self.output_path)
        self.view.emit(
            "fetching",
            {"busy": self.is_fetching, "can_fetch": has_url and not self.is_fetching},
        )
        self.update_card_buttons(has_folder)
        if hasattr(self, "batch_bar"):
            self._refresh_batch_bar()

    # ──────────────────────────────────────────────────────────
    # Core flow
    # ──────────────────────────────────────────────────────────
    def fetch_videos(self, url: str | None = None):
        if self.is_fetching:
            return
        if url is not None:
            self.url_entry.set(url, publish=False)
        raw_url = self.url_entry.get()
        url = raw_url.strip()
        # Normalize and trim URL text before fetch.
        norm_url = normalize_url(url)
        if norm_url != url:
            self.log(f"Using URL: {norm_url}")
            url = norm_url
        if url != raw_url:
            self.url_entry.set(url)
        self.last_url = url
        if not url:
            self.ui_warn("Input Error", "Please enter a URL.")
            return

        self.is_fetching = True
        self.last_action_kind = None
        self.last_selected_format = None
        self.last_selected_label = None
        self.last_selected_title = None
        self.last_transcript_source = None
        self.last_transcript_languages = []
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
                if not self._prepare_cookie_retry():
                    self.ui_queue.put(("results", []))
                    return
                outcome = self.fetcher.fetch(url, username, password)
                if isinstance(outcome, NeedsCookies):
                    self.log("Browser cookie retry did not resolve the fetch error.")
                    self.ui_queue.put(("results", []))
                    return

            if isinstance(outcome, NeedsLogin):
                self.log(str(outcome.error))
                username, password = self.ui_prompt_for_credentials(url, browser_automation=False)
                if not username or not password:
                    self.log("No credentials provided.")
                    self.ui_queue.put(("results", []))
                    return
                outcome = self.fetcher.fetch(url, username, password)

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
                    "Doggybag will open the target site in Chrome, log in with the "
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
        self.view.emit("results_reset", {})
        self.batch_bar.hide()

    def _sanitize_entries(self, entries) -> list[dict]:
        raw_entries = list(entries or [])
        sanitized = [entry for entry in raw_entries if isinstance(entry, dict)]
        dropped = len(raw_entries) - len(sanitized)
        if dropped:
            suffix = "entry" if dropped == 1 else "entries"
            self.log(f"Dropped {dropped} unusable {suffix}.")
        return sanitized

    def set_results_state(self, text: str):
        self.view.emit("results_state", {"text": text or ""})

    def format_duration(self, duration):
        return format_duration_text(duration)

    def _collection_title(self) -> str | None:
        """Playlist name, or the one channel every entry shares."""
        entries = self.video_entries
        if len(entries) < 2:
            return None
        for key in ("playlist_title", "playlist"):
            for entry in entries[:5]:
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("channel", "uploader"):
            values = {entry.get(key) for entry in entries}
            if len(values) == 1:
                value = values.pop()
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def populate_cards(self):
        self.clear_results()
        if not self.video_entries:
            self.set_results_state("No videos found or unsupported site.")
            self.log("No videos found.")
            return
        self.selected_entry_indexes = set(range(len(self.video_entries)))
        self.entry_format_by_index.clear()
        self.entry_transcript_by_index.clear()
        self.view.emit(
            "results",
            {
                "generation": self.entries_generation,
                "total": len(self.video_entries),
                "collection": self._collection_title(),
            },
        )
        if len(self.video_entries) > 1:
            self.batch_bar.show()
        self._refresh_batch_bar()
        self.set_results_state("")
        self.render_next_batch()
        self.log(f"Found {len(self.video_entries)} video(s).")

    def render_next_batch(self):
        if not self.video_entries:
            return
        start = self.rendered_count
        end = min(start + self.batch_size, len(self.video_entries))
        added = []
        for index, entry in enumerate(self.video_entries[start:end], start=start):
            title = entry.get('title') or 'Untitled'
            transcript_options = self.get_transcript_options(entry)
            card = CardModel(
                index=index,
                generation=self.entries_generation,
                title=title,
                details=entry_details(entry),
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
                emit=self.view.emit,
            )
            self.cards.append(card)
            self.card_entries[card] = entry
            self.card_index_by_card[card] = index
            self.card_by_index[index] = card
            added.append(card)

        self.rendered_count = end
        self.update_card_buttons(bool(self.output_path))
        self.view.emit(
            "cards_add",
            {
                "generation": self.entries_generation,
                "items": [card.describe() for card in added if hasattr(card, "describe")],
                "rendered": self.rendered_count,
                "total": len(self.video_entries),
            },
        )

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
        if self.rendered_count < len(self.video_entries):
            self.render_next_batch()

    def get_default_output_path(self) -> str:
        downloads = Path.home() / "Downloads"
        if downloads.exists():
            return str(downloads)
        return str(Path.home())

    # ──────────────────────────────────────────────────────────
    # Frontend intents
    # ──────────────────────────────────────────────────────────
    def card_for(self, generation: int, index: int):
        """Return the live card for a frontend key, or None when stale."""
        if generation != self.entries_generation:
            return None
        card = self.card_by_index.get(index)
        if card is None or not self._card_in_session(card):
            return None
        return card

    def card_intent(self, generation: int, index: int, intent: str, value=None) -> None:
        card = self.card_for(generation, index)
        if card is None:
            return
        if intent == "select":
            card.toggle_selected(bool(value))
        elif intent == "format":
            card.choose_format(str(value))
        elif intent == "transcript_choice":
            card.choose_transcript(value)
        elif intent == "whisper_model":
            card.choose_whisper_model(str(value))
        elif intent == "download":
            card.request_download()
        elif intent == "transcript":
            card.request_transcript()
        elif intent == "whisper":
            card.request_whisper()

    def set_output_folder(self, folder: str | None) -> None:
        if folder:
            self.output_path = str(folder)
            self.footer.set_output_path(self.output_path)
            self.log(f"Output folder changed: {self.output_path}")
        self.update_button_states()

    def browse_folder(self):
        self.set_output_folder(self.view.pick_folder(self.output_path))

    def set_cookies_file(self, path: str | None) -> None:
        self.cookies_path = path or None
        try:
            store_preferred_cookies_path(self.cookies_path)
        except Exception as exc:
            self.log(f"Could not save cookies.txt preference: {exc}")
        footer = getattr(self, "footer", None)
        if footer is not None:
            setter = getattr(footer, "set_cookies_path", None)
            if setter:
                setter(self.cookies_path)
        if self.cookies_path:
            self.log(f"Using cookies.txt: {self.cookies_path}")
        else:
            self.log("Stopped using a cookies.txt file.")

    def browse_cookies(self):
        path = self.view.pick_file("Select cookies.txt", self.cookies_path)
        if path:
            self.set_cookies_file(path)

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
        fmt_id = format_id or self.format_options[0]["format"]
        fmt_label = self.format_label_map.get(fmt_id, fmt_id)
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
                audio_only=is_audio_extract_format(fmt_id),
            )
            return
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
                "Cookies required; choose a browser in the footer and retry Whisper"
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
        acquisition progress and one UI-mediated browser-cookie prompt.
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
                """Main-thread browser-cookie prompt; worker never touches Tk."""
                try:
                    self.log_error("Whisper", cookie_exc.original or cookie_exc)
                except Exception as log_exc:
                    self._log_whisper_worker_event_failure("cookie log", log_exc)
                if self.cookies_browser:
                    self._log_whisper_safely(
                        f"Retrying Whisper audio with saved cookie browser "
                        f"({self.cookies_browser})."
                    )
                    return
                try:
                    chosen = self.choose_cookies_browser()
                except Exception as prompt_exc:
                    self._log_whisper_worker_event_failure(
                        "cookie prompt",
                        prompt_exc,
                    )
                    raise AudioAcquisitionError(
                        "Cookies prompt failed; Whisper audio download was not retried"
                    ) from prompt_exc
                if not chosen:
                    self._log_whisper_safely("Browser cookie source declined.")
                    raise AudioAcquisitionError(
                        "Browser cookie source declined; Whisper audio download was not retried"
                    )
                try:
                    self.set_cookies_browser(chosen)
                except Exception as select_exc:
                    self._log_whisper_worker_event_failure(
                        "cookie select",
                        select_exc,
                    )
                    raise AudioAcquisitionError(
                        "Cookies selection failed; Whisper audio download was not retried"
                    ) from select_exc

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
                            "Cookies still required after browser retry; "
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
                outputs = (str(result.text_path), str(result.srt_path))
                self._queue_whisper_worker_event(
                    "whisper_outputs",
                    (card, request_id, outputs),
                    lambda: self.queue_whisper_outputs(card, request_id, outputs),
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
        audio_only: bool = False,
    ) -> None:
        folder = self.output_path
        if not folder:
            self.ui_warn('Folder Error', 'Select a folder first.')
            return
        if media_epoch is None:
            media_epoch = self.invalidate_verified_media(card)
        safe_title = sanitize_filename(title)
        outfile = unique_output_path(folder, safe_title, "mp3" if audio_only else "mp4")
        path_label = "Direct audio" if audio_only else "Direct HLS"
        self.last_action_kind = "media"
        self.last_selected_format = "ffmpeg-audio" if audio_only else "ffmpeg"
        self.last_selected_label = "Direct audio (MP3)" if audio_only else "Direct HLS"
        self.last_selected_title = title
        self.last_transcript_source = None
        self.last_transcript_languages = []
        self.set_progress(0)
        self.queue_card_busy(card, True)
        self.queue_card_status(card, f"Downloading ({path_label})", state="downloading")
        self.queue_card_progress(card, 0)
        self.log(f"Starting ffmpeg {'audio ' if audio_only else ''}download: {title}")

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
                    audio_only=audio_only,
                )
                verified_files = self._verify_download_outputs([outfile])
                if len(verified_files) == 1:
                    verified = verified_files[0]
                    self.set_progress(1)
                    self.queue_card_progress(card, 1)
                    self.queue_card_status(card, "Complete", state="complete")
                    self.queue_card_outputs(card, [verified.path])
                    self.log(
                        f"ffmpeg download complete: {verified.path} "
                        f"({verified.size:,} bytes)"
                    )
                    self.queue_verified_media(card, verified, media_epoch)
                else:
                    self.set_progress(0)
                    self.queue_card_status(card, f"Failed ({path_label})", state="failed")
                    self.log(
                        "Download verification failed: ffmpeg reported success, "
                        "but Doggybag could not confirm the final file exists."
                    )
                    self.log(f"Unverified output path: {outfile}")
            except Exception as exc:
                self.set_progress(0)
                self.queue_card_status(card, f"Failed ({path_label})", state="failed")
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
        wants_audio = is_audio_extract_format(format_id)
        ffmpeg_ok = bool(getattr(self, "ffmpeg_available", True))
        extract_audio = wants_audio and ffmpeg_ok
        if wants_audio and not ffmpeg_ok:
            self.log("ffmpeg missing: saving audio in its original format instead of MP3.")
        try:
            self.log(
                "Download runtime: "
                f"pid={os.getpid()} exe={sys.executable} cwd={os.getcwd()} "
                f"out_path={out_path} outtmpl={outtmpl}"
            )
            self.log(f"Output folder before download: {self._folder_snapshot(out_path)}")
            try:
                ok = self.download_manager.download(
                    url,
                    format_id,
                    outtmpl,
                    progress_hook=hook,
                    post_hook=post_hook,
                    extract_audio=extract_audio,
                )
            except CookiesRequiredError as exc:
                self.log_error("Download", exc.original or exc)
                if self._prepare_cookie_retry():
                    try:
                        ok = self.download_manager.download(
                            url,
                            format_id,
                            outtmpl,
                            progress_hook=hook,
                            post_hook=post_hook,
                            extract_audio=extract_audio,
                        )
                    except CookiesRequiredError as retry_exc:
                        self.log_error("Download", retry_exc.original or retry_exc)
                        ok = False
                else:
                    ok = False
            if not ok and self.last_error_reason == "format unavailable" and format_id != "best":
                if self.confirm_format_fallback(extract_audio=extract_audio):
                    fallback_id = "best"
                    if extract_audio:
                        fallback_label = self.format_label_map.get(format_id, "Audio (MP3)")
                        self.log("Retrying by extracting MP3 from the best available file.")
                    else:
                        fallback_label = self.format_label_map.get(fallback_id, "Best (single file)")
                        self.log(f"Retrying download with {fallback_label} due to unavailable format.")
                    self.last_selected_format = format_id if extract_audio else fallback_id
                    self.last_selected_label = fallback_label
                    if card:
                        self.set_progress(0)
                        self.queue_card_progress(card, 0)
                        self.queue_card_status(card, f"Retrying ({fallback_label})", state="downloading")
                    try:
                        ok = self.download_manager.download(
                            url,
                            fallback_id,
                            outtmpl,
                            progress_hook=hook,
                            post_hook=post_hook,
                            extract_audio=extract_audio,
                        )
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
                        self.queue_card_outputs(card, [output.path for output in verified_files])
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
                        "but Doggybag could not confirm the final file exists."
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

    def _recent_transcript_outputs(self, folder: str, started_at: float) -> list[str]:
        try:
            return [
                path
                for path in self._recent_download_outputs(folder, started_at)
                if ".transcript." in Path(path).name
            ]
        except Exception:
            return []

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
        started_at = time.time()

        def mark_success(result):
            nonlocal success
            success = True
            self.last_transcript_source = result.source
            self.last_transcript_languages = list(result.languages)
            self.set_progress(1)
            if card:
                self.queue_card_progress(card, 1)
                self.queue_card_status(card, f"Transcript saved ({result.source})", state="complete")
                self.queue_card_outputs(card, self._recent_transcript_outputs(out_path, started_at))
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
            if self._prepare_cookie_retry():
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
            elif card:
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
        run = self.batch_run
        if run is not None:
            run.cancel.set()
        self.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)

    # ──────────────────────────────────────────────────────────
    # Auth helpers / Selenium fallback
    # ──────────────────────────────────────────────────────────
    def prompt_for_credentials(self, url: str, *, browser_automation: bool = True):
        username, password = dev_credentials_for_url(url, self.dev_defaults)
        if username and password:
            domain = dev_domain_for_url(url, self.dev_defaults)
            self.log(f"[dev] Using developer.json credentials for {domain}.")
            return username, password

        site = site_label(url)
        if browser_automation:
            title = f"Sign in to {site}" if site else "Sign in"
            message = (
                "Doggybag will open the site in Chrome, sign in with these "
                "credentials, then look for playable media."
            )
        else:
            title = f"Sign in to {site}" if site else "Sign in"
            message = (
                "Doggybag signs in directly (no browser window), loads the video, "
                "then signs out. Credentials are not saved."
            )
        answer = self._ask("credentials", {"title": title, "message": message, "site": site})
        if not isinstance(answer, dict):
            return None, None
        username = str(answer.get("username") or "").strip()
        password = str(answer.get("password") or "")
        if not username or not password:
            return None, None
        return username, password

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


__all__ = ["AppController", "BatchItem", "BatchPromptPolicy", "BatchRun", "entry_details", "site_label"]
