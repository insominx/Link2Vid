import customtkinter as ctk
from tkinter import TclError, filedialog, messagebox, simpledialog
from concurrent.futures import ThreadPoolExecutor
import threading
import queue
import os
import shutil
import json
import time
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..core.dev_defaults import dev_credentials_for_url, dev_domain_for_url, resolve_login_plan
from ..core import (
    CookiesRequiredError,
    DirectHlsFound,
    DownloadManager,
    NoTranscriptAvailableError,
    FetchError,
    FetchResults,
    NeedsCookies,
    NeedsSelenium,
    VideoFetcher,
    build_diagnostics,
    classify_error,
    get_error_guidance,
    download_with_ffmpeg,
    get_format_options,
    get_yt_dlp_version,
    normalize_url,
    sanitize_filename,
    unique_output_path,
    selenium_fetch_media_entries,
    url_from_clipboard_text,
)
from ..core.extractors import build_media_entries, title_from_page_url
from .components import FooterBar, LogDrawer, VideoCard
from .thumbnail_loader import ThumbnailLoader

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


@dataclass(frozen=True)
class VerifiedOutput:
    path: str
    size: int
    modified_at: float


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
    def __init__(self, root):
        self.root = root
        self.root.title('Video Downloader (Cards UI)')
        self.root.geometry('1100x800')
        self.root.minsize(900, 700)
        self.video_entries = []
        self.cards = []
        self.card_entries = {}
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

        results_frame = ctk.CTkFrame(main_frame)
        results_frame.pack(fill="both", expand=True, pady=(0, 16))
        self.results_scroll = ctk.CTkScrollableFrame(results_frame)
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
                    self.video_entries = payload
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
        for card in self.cards:
            card.destroy()
        self.cards = []
        self.card_entries = {}
        self.busy_cards.clear()
        self.rendered_count = 0
        if self.load_more_button.winfo_manager():
            self.load_more_button.pack_forget()

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
        for entry in self.video_entries[start:end]:
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
                on_download=self.handle_card_download,
                on_transcript=self.handle_card_transcript,
            )
            card.pack(fill="x", padx=8, pady=8)
            if transcript_options == []:
                card.set_status("No transcript tracks", state="ready")
            self.cards.append(card)
            self.card_entries[card] = entry
            placeholder = self.get_placeholder_image(source_label)
            card.set_thumbnail_image(self.make_ctk_image(placeholder))
            self.queue_thumbnail(card, entry, source_label)

        self.rendered_count = end
        if self.rendered_count < len(self.video_entries):
            self.load_more_button.pack(pady=(8, 12))

        self.update_card_buttons(bool(self.output_path))

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

    def handle_card_download(self, card, format_id):
        entry = self.card_entries.get(card)
        if not entry:
            return
        folder = self.output_path
        if not folder:
            self.ui_warn('Folder Error', 'Select a folder first.')
            return
        url = entry.get('webpage_url', self.url_entry.get().strip())
        ffmpeg_headers = entry.get("_ffmpeg_headers")
        if ffmpeg_headers:
            self._download_direct_media(card, url, ffmpeg_headers, entry.get('title', 'Video'))
            return
        fmt_id = format_id or self.format_options[0]["format"]
        fmt_label = self.format_label_map.get(fmt_id, fmt_id)
        if not self.ffmpeg_available and '+' in fmt_id:
            choice = self.ui_ffmpeg_fallback(fmt_label)
            if choice == "cancel":
                return
            if choice == "fallback":
                fmt_id = "best"
                fmt_label = self.format_label_map.get(fmt_id, "Best (single file)")
                self.log("ffmpeg missing: switching to Best (single file) for compatibility.")
            else:
                self.log("ffmpeg missing: proceeding without merge support (may fail or be video-only).")
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
            args=(url, fmt_id, folder, card, fmt_label),
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
        url = entry.get('webpage_url', self.url_entry.get().strip())
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

    def _download_direct_media(self, card, media_url: str, headers: dict, title: str) -> None:
        folder = self.output_path
        if not folder:
            self.ui_warn('Folder Error', 'Select a folder first.')
            return
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
                self.set_progress(1)
                self.queue_card_progress(card, 1)
                self.queue_card_status(card, "Complete", state="complete")
                self.log(f"ffmpeg download complete: {outfile}")
            except Exception as exc:
                self.set_progress(0)
                self.queue_card_status(card, "Failed (Direct HLS)", state="failed")
                self.log_error("ffmpeg", exc)
            finally:
                self.queue_card_busy(card, False)

        threading.Thread(target=run_ffmpeg, daemon=True).start()

    def download_video(self, url, format_id, out_path, card=None, fmt_label=None):
        finished_logged = False
        saved_files = []

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
                if self.ui_confirm(
                    "Cookies required",
                    "This video may require cookies (age/consent/bot checks).\n"
                    "Automatic browser-cookie retry did not finish successfully.\n\n"
                    "Select a cookies.txt file to retry?",
                ):
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
                if self.ui_confirm(
                    "Format unavailable",
                    "The selected format is not available for this video.\n"
                    "Retry with Best (single file)?",
                ):
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

    def _verify_download_outputs(self, paths: list[str]) -> list[VerifiedOutput]:
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
                    verified.append(VerifiedOutput(str(file_path), size, stat.st_mtime))
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

        def mark_success(result):
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
            if self.ui_confirm(
                "Cookies required",
                "This transcript may require cookies (age/consent/bot checks).\n"
                "Automatic browser-cookie retry did not finish successfully.\n\n"
                "Select a cookies.txt file to retry?",
            ):
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
                        return
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

        username = simpledialog.askstring(
            "Login Required",
            "Enter email or username for this site:",
            parent=self.root,
        )
        if username is None:
            return None, None
        password = simpledialog.askstring(
            "Login Required",
            "Enter password:",
            parent=self.root,
            show="*",
        )
        if password is None:
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


__all__ = ["VideoDownloaderApp"]
