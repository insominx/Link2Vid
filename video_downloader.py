import customtkinter as ctk
from tkinter import filedialog, messagebox, simpledialog
from concurrent.futures import ThreadPoolExecutor
import threading
import queue
import os
import shutil
import sys
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from link2vid.core import (
    DownloadManager,
    download_with_ffmpeg,
    extract_linkedin_videos,
    get_format_options,
    get_yt_dlp_version,
    normalize_url,
    scan_direct_m3u8,
    selenium_fetch_m3u8,
)
from link2vid.ui import FooterBar, LogDrawer, ThumbnailLoader, VideoCard

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


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
        self.dev_defaults = self.load_dev_defaults()
        self.main_thread_id = threading.get_ident()
        self.ui_queue = queue.Queue()
        self.log_history = []
        self.log_max = 200
        self.last_error = None
        self.last_selected_format = None
        self.last_selected_label = None
        self.last_selected_title = None
        self.last_url = None
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.fetch_future = None
        self.is_fetching = False
        self.batch_size = 20
        self.rendered_count = 0
        self.thumbnail_size = (120, 72)
        self.thumbnail_styles = {
            "youtube": ("YouTube", ("#b91c1c", "#f97316")),
            "vimeo": ("Vimeo", ("#0891b2", "#67e8f9")),
            "tiktok": ("TikTok", ("#111827", "#ef4444")),
            "twitter": ("X", ("#111827", "#6b7280")),
            "x": ("X", ("#111827", "#6b7280")),
            "linkedin": ("LinkedIn", ("#0a66c2", "#60a5fa")),
            "default": ("Video", ("#4b5563", "#9ca3af")),
        }
        self.placeholder_cache = {}
        self.placeholder_font = None
        self.output_path = self.get_default_output_path()
        self.cookies_path = None
        self.ydl_logger = YtDlpLogger(self)
        self.download_manager = DownloadManager(
            ydl_logger=self.ydl_logger,
            log=self.log,
            log_error=self.log_error,
            dev_defaults=self.dev_defaults,
            get_cookies_path=lambda: self.cookies_path,
            select_cookies=self.ui_select_cookies,
            confirm=self.ui_confirm,
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

        self.log(f"UI build: cards ({os.path.abspath(__file__)})")

        if self.dev_defaults.get('use_defaults') and self.dev_defaults.get('default_url'):
            self.url_entry.insert(0, self.dev_defaults['default_url'])

        self.root.after(100, self.process_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_button_states()
        self.log(f"yt-dlp version: {get_yt_dlp_version()}")
        self.check_ffmpeg()
        self.check_js_runtime()
        self.log("Tip: cookies.txt can help with YouTube/X restricted videos.")

    # ──────────────────────────────────────────────────────────
    # Utility / Logging
    # ──────────────────────────────────────────────────────────
    def _append_log(self, message):
        if not message:
            return
        self.log_history.append(message)
        if len(self.log_history) > self.log_max:
            self.log_history = self.log_history[-self.log_max:]
        if hasattr(self, "log_drawer"):
            self.log_drawer.append(message)

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

    def ui_warn(self, title: str, message: str) -> None:
        self.run_on_ui_thread(lambda: messagebox.showwarning(title, message))

    def ui_select_cookies(self) -> None:
        self.run_on_ui_thread(self.browse_cookies)

    def ui_prompt_for_credentials(self):
        return self.run_on_ui_thread(self.prompt_for_credentials)

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

    def format_error(self, stage, err):
        err_text = str(err) or "Unknown error"
        hint = self.error_hint(err_text)
        return f"[{stage}] {type(err).__name__}: {err_text}{hint}"

    def error_hint(self, err_text):
        msg = err_text.lower()
        if any(k in msg for k in ["signature", "cipher", "extractor", "unable to extract"]):
            return " (Hint: try updating yt-dlp.)"
        if "ffmpeg" in msg:
            return " (Hint: check ffmpeg is installed and on PATH.)"
        if "javascript runtime" in msg or "js runtime" in msg:
            return " (Hint: install deno or node and configure yt-dlp JS runtime.)"
        if any(k in msg for k in ["403", "forbidden", "bot", "sign in", "age", "cookie", "consent"]):
            return " (Hint: try cookies.txt from your browser.)"
        return ""

    def log_error(self, stage, err):
        msg = self.format_error(stage, err)
        self.last_error = msg
        self.log(msg)

    def check_ffmpeg(self):
        self.ffmpeg_path = shutil.which("ffmpeg")
        self.ffmpeg_available = bool(self.ffmpeg_path)
        if not self.ffmpeg_available:
            self.log("ffmpeg not found on PATH. Some formats may fail or be video-only.")

    def check_js_runtime(self):
        runtimes = ["deno", "node", "bun"]
        self.js_runtimes = [rt for rt in runtimes if shutil.which(rt)]
        if not self.js_runtimes:
            self.log("No JavaScript runtime detected (deno/node/bun). Some YouTube formats may be missing.")

    def copy_diagnostics(self):
        url = self.url_entry.get().strip()
        js_runtime = ", ".join(self.js_runtimes) if getattr(self, "js_runtimes", None) else "not found"
        lines = [
            "Link2Vid Diagnostics",
            f"URL: {url or 'n/a'}",
            f"Selected title: {self.last_selected_title or 'n/a'}",
            f"Selected format: {self.last_selected_format or 'n/a'}",
            f"yt-dlp version: {get_yt_dlp_version()}",
            f"ffmpeg: {self.ffmpeg_path or 'not found'}",
            f"JS runtime: {js_runtime}",
            f"Last error: {self.last_error or 'n/a'}",
            "-- Recent log --",
            *self.log_history[-20:]
        ]
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
        # Normalize known URL variants (e.g., x.com → twitter.com)
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
            # DEV defaults
            username = password = None
            if self.dev_defaults.get('use_defaults') and "gdcvault.com" in url.lower():
                username = self.dev_defaults.get('default_username')
                password = self.dev_defaults.get('default_password')

            # ── 1. yt‑dlp first ───────────────────────────────────
            try:
                entries = self.get_video_info(url, username, password)
                self.ui_queue.put(("results", entries))
                return
            except Exception as exc:
                self.log_error("yt-dlp", exc)

            # ── 2. LinkedIn OpenGraph / JSON scrape ────────────────
            entries = self.try_linkedin_video(url)
            if entries:
                self.ui_queue.put(("results", entries))
                return

            # ── 3. Direct HLS scrape (no browser) ─────────────────
            if self.try_direct_m3u8(url):
                self.ui_queue.put(("results_state", "Direct HLS path used → see log"))
                return

            # ── 4. Selenium fallback ──────────────────────────────
            if self.ui_confirm("Fallback", "Automatic scrape failed. Try browser automation?"):
                username, password = self.ui_prompt_for_credentials()
                if not username or not password:
                    self.log("No credentials provided.")
                    return
                m3u8_url = self.selenium_fallback(url, username, password)
                if m3u8_url:
                    self.log(f"Found playlist: {m3u8_url}")
                    if self.ui_confirm("Download", "Download with ffmpeg?"):
                        folder = self.output_path
                        if not folder:
                            self.ui_warn("Folder Error", "Please select a folder first.")
                            return
                        outfile = os.path.join(folder, "video.mp4")
                        threading.Thread(
                            target=download_with_ffmpeg,
                            args=(m3u8_url, outfile, {"Referer": url}),
                            daemon=True,
                        ).start()
                else:
                    self.log("No playlist found with Selenium.")

            self.ui_queue.put(("results", []))
        finally:
            self.ui_queue.put(("fetch_done", None))

    # ──────────────────────────────────────────────────────────
    # Direct HLS helper
    # ──────────────────────────────────────────────────────────
    def try_direct_m3u8(self, page_url):                          #  >>> NEW
        result = scan_direct_m3u8(page_url, log=self.log)
        if not result:
            return False
        if self.ui_confirm("Download", "Download with ffmpeg now?"):
            folder = self.output_path
            if not folder:
                self.ui_warn("Folder", "Choose a download folder first.")
                return True
            outfile = os.path.join(folder, "gdcvault.mp4")
            self.log(f"Starting ffmpeg → {outfile}")
            threading.Thread(
                target=download_with_ffmpeg,
                args=(result.playlist_url, outfile, result.headers),
                daemon=True,
            ).start()
        return True

    # ──────────────────────────────────────────────────────────
    # LinkedIn helper (OpenGraph / JSON)
    # ──────────────────────────────────────────────────────────
    def try_linkedin_video(self, page_url):
        """Return True if at least one direct video URL was found on a LinkedIn post page."""
        return extract_linkedin_videos(page_url, log=self.log) or []

    # ──────────────────────────────────────────────────────────
    # yt‑dlp path
    # ──────────────────────────────────────────────────────────
    def get_video_info(self, url, username=None, password=None):
        return self.download_manager.get_video_info(url, username, password)

    def clear_results(self):
        for card in self.cards:
            card.destroy()
        self.cards = []
        self.card_entries = {}
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
        try:
            total = int(duration)
        except (TypeError, ValueError):
            return None
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

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
            card = VideoCard(
                self.results_scroll,
                title=title,
                metadata=metadata,
                format_options=self.format_options,
                on_download=self.handle_card_download,
            )
            card.pack(fill="x", padx=8, pady=8)
            self.cards.append(card)
            self.card_entries[card] = entry
            placeholder = self.get_placeholder_image(source_label)
            card.set_thumbnail_image(self.make_ctk_image(placeholder))
            self.queue_thumbnail(card, entry, source_label)

        self.rendered_count = end
        if self.rendered_count < len(self.video_entries):
            self.load_more_button.pack(pady=(8, 12))

        self.update_card_buttons(bool(self.output_path))

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
        extractor = (entry.get("extractor_key") or entry.get("extractor") or "").lower()
        url = (entry.get("webpage_url") or "").lower()
        text = extractor or url
        if "youtube" in text:
            return "youtube"
        if "vimeo" in text:
            return "vimeo"
        if "tiktok" in text:
            return "tiktok"
        if "twitter" in text or "x.com" in text:
            return "x"
        if "linkedin" in text:
            return "linkedin"
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
        self.update_button_states()

    def browse_cookies(self):
        path = filedialog.askopenfilename(title='Select cookies.txt',
                                          filetypes=[('Text files', '*.txt'), ('All files', '*.*')])
        if path:
            self.cookies_path = path
            self.footer.set_cookies_path(path)

    def update_card_buttons(self, has_folder: bool):
        state = "normal" if has_folder else "disabled"
        for card in self.cards:
            card.download_button.configure(state=state)

    def handle_card_download(self, card, format_id):
        entry = self.card_entries.get(card)
        if not entry:
            return
        folder = self.output_path
        if not folder:
            self.ui_warn('Folder Error', 'Select a folder first.')
            return
        url = entry.get('webpage_url', self.url_entry.get().strip())
        fmt_id = format_id or self.format_options[0]["format"]
        fmt_label = self.format_label_map.get(fmt_id, fmt_id)
        self.last_selected_format = fmt_id
        self.last_selected_label = fmt_label
        self.last_selected_title = entry.get('title', 'Video')
        if not self.ffmpeg_available and '+' in fmt_id:
            if not self.ui_confirm('ffmpeg missing', 'ffmpeg is required to merge audio/video. Continue anyway?'):
                return
        self.set_progress(0)
        self.queue_card_status(card, f"Downloading ({fmt_label})", state="downloading")
        self.queue_card_progress(card, 0)
        self.log(f"Starting download: {entry.get('title', 'Video')} ({fmt_label})")
        threading.Thread(
            target=self.download_video,
            args=(url, fmt_id, folder, card, fmt_label),
            daemon=True,
        ).start()

    def download_video(self, url, format_id, out_path, card=None, fmt_label=None):
        finished_logged = False
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
                self.log('Download complete!')
                if card:
                    self.queue_card_status(card, "Complete", state="complete")
        outtmpl = os.path.join(out_path, '%(title)s.%(ext)s')
        ok = self.download_manager.download(url, format_id, outtmpl, progress_hook=hook)
        if not ok and card:
            label = fmt_label or format_id
            self.queue_card_status(card, f"Failed ({label})", state="failed")

    def on_close(self):
        try:
            self.thumbnail_loader.clear_cache(remove_dir=False)
        finally:
            self.root.destroy()

    # ──────────────────────────────────────────────────────────
    # Auth helpers / Selenium fallback
    # ──────────────────────────────────────────────────────────
    def prompt_for_credentials(self):
        # Use dev defaults if enabled
        if hasattr(self, 'dev_defaults') and self.dev_defaults.get('use_defaults'):
            username = self.dev_defaults.get('default_username')
            password = self.dev_defaults.get('default_password')
            print(f"[DEBUG] Using dev defaults for credentials: {username}")
            return username, password
        username = simpledialog.askstring("Login Required", "Enter username:", parent=self.root)
        if username is None:
            return None, None
        password = simpledialog.askstring("Login Required", "Enter password:", parent=self.root, show='*')
        if password is None:
            return None, None
        return username, password

    def selenium_fallback(self, page_url, username=None, password=None):
        if hasattr(self, 'dev_defaults') and self.dev_defaults.get('use_defaults'):
            username = self.dev_defaults.get('default_username')
            password = self.dev_defaults.get('default_password')
            print(f"[DEBUG] Using dev defaults for selenium login: {username}")
        result = selenium_fetch_m3u8(page_url, username, password, log=self.log)
        if result:
            return result
        self.log("Selenium fallback error: failed to find m3u8")
        return None

    # ──────────────────────────────────────────────────────────
    # Config load
    # ──────────────────────────────────────────────────────────
    def load_dev_defaults(self):
        try:
            with open('developer.json', 'r') as f:
                return json.load(f)
        except Exception:
            return {}


# ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    root = ctk.CTk()
    app = VideoDownloaderApp(root)
    root.mainloop()
    sys.exit(0)
