import customtkinter as ctk
from tkinter import filedialog, messagebox, simpledialog
import threading
import queue
import yt_dlp
import os
import shutil
import tkinter as tk
import sys
import requests
from bs4 import BeautifulSoup
import subprocess
import re
import m3u8                                   #  >>> NEW
import urllib.parse                           #  >>> NEW
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import time
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import json

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
        self.root.title('Video Downloader')
        self.root.geometry('1100x800')
        self.root.minsize(900, 700)
        self.video_entries = []
        self.display_entries = []
        self.selected_index = None
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
        self.ydl_logger = YtDlpLogger(self)

        font_big = ("Arial", 22)
        font_med = ("Arial", 16)
        font_small = ("Consolas", 12)

        main_frame = ctk.CTkFrame(root)
        main_frame.pack(fill="both", expand=True, padx=30, pady=30)

        url_label = ctk.CTkLabel(main_frame, text='Enter URL:', font=font_big)
        url_label.pack(anchor='w', pady=(0, 8))
        self.url_entry = ctk.CTkEntry(main_frame, width=600, font=font_med)
        self.url_entry.pack(fill="x", pady=(0, 12))
        self.url_entry.bind('<KeyRelease>', self.update_button_states)
        self.fetch_btn = ctk.CTkButton(
            main_frame, text='Fetch Videos',
            command=self.fetch_videos, font=font_med,
            height=40, width=200, state="disabled")
        self.fetch_btn.pack(pady=(0, 18))

        listbox_frame = ctk.CTkFrame(main_frame)
        listbox_frame.pack(fill="both", expand=True, pady=(0, 18))
        self.listbox = tk.Listbox(listbox_frame, width=120, height=12, font=font_small)
        self.listbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.listbox.bind('<<ListboxSelect>>', self.on_select)

        folder_row = ctk.CTkFrame(main_frame)
        folder_row.pack(fill="x", pady=(0, 18))
        ctk.CTkLabel(folder_row, text='Select download folder:', font=font_big).grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.folder_entry = ctk.CTkEntry(folder_row, width=400, font=font_med)
        self.folder_entry.grid(row=0, column=1, padx=(0, 10))
        self.folder_entry.bind('<KeyRelease>', self.update_button_states)
        self.browse_btn = ctk.CTkButton(
            folder_row, text='Browse', command=self.browse_folder,
            font=font_med, height=32, width=100)
        self.browse_btn.grid(row=0, column=2)
        folder_row.grid_columnconfigure(1, weight=1)

        # Optional cookies row (YouTube/X/etc)
        cookies_row = ctk.CTkFrame(main_frame)
        cookies_row.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(cookies_row, text='Optional cookies.txt (YouTube/X/etc):', font=font_big).grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.cookies_entry = ctk.CTkEntry(cookies_row, width=400, font=font_med)
        self.cookies_entry.grid(row=0, column=1, padx=(0, 10))
        self.cookies_btn = ctk.CTkButton(
            cookies_row, text='Select cookies.txt', command=self.browse_cookies,
            font=font_med, height=32, width=170)
        self.cookies_btn.grid(row=0, column=2)
        cookies_row.grid_columnconfigure(1, weight=1)

        self.cookies_path = None
        if self.dev_defaults.get('cookies_path'):
            self.cookies_path = self.dev_defaults.get('cookies_path')
            self.cookies_entry.insert(0, self.cookies_path)

        self.download_btn = ctk.CTkButton(
            main_frame, text='Download Selected',
            command=self.download_selected, font=font_big,
            height=50, width=300, state="disabled")
        self.download_btn.pack(pady=(0, 18))

        self.progress = ctk.DoubleVar()
        self.progress_bar = ctk.CTkProgressBar(main_frame, variable=self.progress, width=800, height=24)
        self.progress_bar.pack(fill="x", pady=(0, 18))
        self.progress_bar.set(0)

        self.diagnostics_btn = ctk.CTkButton(
            main_frame, text='Copy Diagnostics',
            command=self.copy_diagnostics, font=font_med,
            height=32, width=200)
        self.diagnostics_btn.pack(pady=(0, 12))

        self.output_text = ctk.CTkTextbox(main_frame, height=120, font=font_small)
        self.output_text.pack(fill="both", expand=True, pady=(0, 0))
        self.output_text.configure(state='disabled')

        if self.dev_defaults.get('use_defaults') and self.dev_defaults.get('default_url'):
            self.url_entry.insert(0, self.dev_defaults['default_url'])

        self.root.after(100, self.process_ui_queue)
        self.update_button_states()
        self.log(f"yt-dlp version: {self.get_yt_dlp_version()}")
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
        self.output_text.configure(state='normal')
        self.output_text.insert('end', message + '\n')
        self.output_text.see('end')
        self.output_text.configure(state='disabled')

    def log(self, message):
        if threading.get_ident() != self.main_thread_id:
            self.ui_queue.put(("log", message))
            return
        self._append_log(message)

    def process_ui_queue(self):
        try:
            while True:
                action, payload = self.ui_queue.get_nowait()
                if action == "log":
                    self._append_log(payload)
                elif action == "progress":
                    self.progress.set(payload)
                    self.progress_bar.set(payload)
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

    def get_yt_dlp_version(self):
        version = getattr(yt_dlp, "__version__", None)
        if version:
            return version
        try:
            from yt_dlp import version as ytdlp_version
            return getattr(ytdlp_version, "__version__", getattr(ytdlp_version, "VERSION", "unknown"))
        except Exception:
            return "unknown"

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

    def get_format_options(self, entry):
        return [
            {"label": "Best (A+V)", "format": "bestvideo+bestaudio/best"},
            {"label": "Best video", "format": "bestvideo"},
            {"label": "Best audio", "format": "bestaudio"}
        ]

    def copy_diagnostics(self):
        url = self.url_entry.get().strip()
        js_runtime = ", ".join(self.js_runtimes) if getattr(self, "js_runtimes", None) else "not found"
        lines = [
            "Link2Vid Diagnostics",
            f"URL: {url or 'n/a'}",
            f"Selected title: {self.last_selected_title or 'n/a'}",
            f"Selected format: {self.last_selected_format or 'n/a'}",
            f"yt-dlp version: {self.get_yt_dlp_version()}",
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
        folder = self.folder_entry.get().strip()
        has_url = bool(url)
        has_folder = bool(folder)
        has_selection = self.selected_index is not None
        self.fetch_btn.configure(state="normal" if has_url else "disabled")
        self.download_btn.configure(state="normal" if has_selection and has_folder else "disabled")

    # ──────────────────────────────────────────────────────────
    # Core flow
    # ──────────────────────────────────────────────────────────
    def fetch_videos(self):
        url = self.url_entry.get().strip()
        # Normalize known URL variants (e.g., x.com → twitter.com)
        norm_url = self.normalize_url(url)
        if norm_url != url:
            self.log(f"Using URL: {norm_url}")
            url = norm_url
        self.last_url = url
        if not url:
            messagebox.showwarning('Input Error', 'Please enter a URL.')
            return

        # DEV defaults
        username = password = None
        if self.dev_defaults.get('use_defaults') and "gdcvault.com" in url.lower():
            username = self.dev_defaults.get('default_username')
            password = self.dev_defaults.get('default_password')

        self.listbox.delete(0, 'end')
        self.listbox.insert('end', 'Fetching...')
        self.root.update()

        # ── 1. yt‑dlp first ───────────────────────────────────
        try:
            self.video_entries = self.get_video_info(url, username, password)
            self.populate_listbox()
            return
        except Exception as e:
            self.log_error("yt-dlp", e)

        # ── 2. LinkedIn OpenGraph / JSON scrape ────────────────
        if self.try_linkedin_video(url):
            # Entries + listbox handled inside helper
            return

        # ── 3. Direct HLS scrape (no browser) ─────────────────
        ok = self.try_direct_m3u8(url)
        if ok:
            self.listbox.insert('end', "Direct HLS path used → see log")
            self.listbox.itemconfig('end', fg='cyan')
            self.selected_index = None
            self.update_button_states()
            return

        # ── 4. Selenium fallback ──────────────────────────────
        if messagebox.askyesno("Fallback", "Automatic scrape failed. Try browser automation?"):
            username, password = self.prompt_for_credentials()
            if not username or not password:
                self.log("No credentials provided.")
                return
            m3u8_url = self.selenium_fallback(url, username, password)
            if m3u8_url:
                self.log(f"Found playlist: {m3u8_url}")
                if messagebox.askyesno("Download", "Download with ffmpeg?"):
                    folder = self.folder_entry.get().strip()
                    if not folder:
                        messagebox.showwarning('Folder Error', 'Please select a folder first.')
                        return
                    outfile = os.path.join(folder, "video.mp4")
                    threading.Thread(target=download_with_ffmpeg,
                                     args=(m3u8_url, outfile, {"Referer": url}),
                                     daemon=True).start()
            else:
                self.log("No playlist found with Selenium.")

        self.selected_index = None
        self.update_button_states()

    # ──────────────────────────────────────────────────────────
    # Direct HLS helper
    # ──────────────────────────────────────────────────────────
    def try_direct_m3u8(self, page_url):                          #  >>> NEW
        """
        Return True if a playlist was handled (download kicked off or ready),
        False if nothing found (so caller can fall back).
        """
        self.log("[HLS] Scanning page for .m3u8 …")
        try:
            sess = requests.Session()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0)",
                "Referer": page_url
            }
            html = sess.get(page_url, headers=headers, timeout=15).text

            # Follow the Blaze iframe
            iframe = re.search(r'<iframe[^>]+src="([^"]+blazestreaming[^"]+)"', html, re.I)
            if iframe:
                iframe_url = urllib.parse.urljoin(page_url, iframe.group(1))
                html = sess.get(iframe_url, headers=headers, timeout=15).text

            m = re.search(r'https?://[^"\']+\.m3u8', html)
            if not m:
                self.log("[HLS] No playlist text found.")
                return False

            m3u8_url = m.group(0)
            self.log(f"[HLS] Found playlist:\n{m3u8_url}")

            # Parse for a quick variant list
            pl = m3u8.load(m3u8_url, headers=headers)
            if pl.is_variant:
                self.log("Available variants:")
                for v in pl.playlists:
                    bw = v.stream_info.bandwidth // 1000
                    res = v.stream_info.resolution
                    self.log(f" • {bw} kbps  {res}  →  {v.uri}")

            # Offer download
            if messagebox.askyesno("Download", "Download with ffmpeg now?"):
                folder = self.folder_entry.get().strip()
                if not folder:
                    messagebox.showwarning("Folder", "Choose a download folder first.")
                    return True
                outfile = os.path.join(folder, "gdcvault.mp4")
                self.log(f"Starting ffmpeg → {outfile}")
                threading.Thread(target=download_with_ffmpeg,
                                 args=(m3u8_url, outfile, headers),
                                 daemon=True).start()
            return True
        except Exception as e:
            self.log(f"[HLS] {e}")
            return False

    # ──────────────────────────────────────────────────────────
    # LinkedIn helper (OpenGraph / JSON)
    # ──────────────────────────────────────────────────────────
    def try_linkedin_video(self, page_url):
        """Return True if at least one direct video URL was found on a LinkedIn post page."""
        if "linkedin.com" not in page_url:
            return False
        self.log("[LinkedIn] Scanning page for direct video links …")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0)"  # simple desktop UA
            }
            html = requests.get(page_url, headers=headers, timeout=15).text

            # Unescape any \u002F sequences that appear in LinkedIn's JSON blobs
            html = bytes(html, "utf-8").decode("unicode_escape", errors="ignore")

            # Try common patterns that LinkedIn uses to embed video URLs
            candidates = []

            # 1) OpenGraph
            og_matches = re.findall(r'<meta[^>]+property="og:video(?:[:_][^\"]+)?"[^>]+content="([^"]+)"', html, re.I)
            candidates.extend(og_matches)

            # 2) Progressive / playbackUrl JSON keys
            json_matches = re.findall(r'"(?:progressiveUrl|playbackUrl)":"(https:[^\"]+?\.mp4[^"]*)"', html)
            candidates.extend(json_matches)

            # 3) m3u8 manifests (rare)
            m3u8_matches = re.findall(r'(https:[^\"]+?\.m3u8[^"]*)', html)
            candidates.extend(m3u8_matches)

            # Clean duplicates and unsafe escapes
            video_urls = []
            for u in candidates:
                u = u.replace("\\u002F", "/")
                if u not in video_urls:
                    video_urls.append(u)

            if not video_urls:
                self.log("[LinkedIn] No direct video links found.")
                return False

            self.video_entries = []
            for idx, vurl in enumerate(video_urls):
                ext = "m3u8" if vurl.endswith(".m3u8") else "mp4"
                entry = {
                    "title": f"LinkedIn video {idx+1}",
                    "formats": [{
                        "format_id": "best",
                        "ext": ext,
                        "filesize_approx": "N/A"
                    }],
                    "webpage_url": vurl
                }
                self.video_entries.append(entry)
            self.populate_listbox()
            return True
        except Exception as e:
            self.log(f"[LinkedIn] {e}")
            return False

    # ──────────────────────────────────────────────────────────
    # yt‑dlp path
    # ──────────────────────────────────────────────────────────
    def get_video_info(self, url, username=None, password=None):
        ydl_opts = {'quiet': True, 'skip_download': True, 'logger': self.ydl_logger}
        if username and password:
            ydl_opts['username'] = username
            ydl_opts['password'] = password
        # If user provided cookies.txt, prefer using it from the start
        if getattr(self, 'cookies_path', None):
            ydl_opts['cookiefile'] = self.cookies_path

        # First attempt: normal extraction
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if 'entries' in info:
                    return info['entries']
                return [info]
        except Exception as first_err:
            # For X/Twitter, retry with browser cookies which are often required
            if ('twitter.com' in url.lower()) or ('x.com' in url.lower()):
                self.log('[yt-dlp] Retry with cookies from browser for X/Twitter…')
                browser = None
                try:
                    # Optional developer preference
                    if hasattr(self, 'dev_defaults'):
                        browser = self.dev_defaults.get('cookies_browser')
                    if not browser:
                        browser = 'edge' if sys.platform.startswith('win') else 'chrome'
                    retry_opts = dict(ydl_opts)
                    retry_opts['cookiesfrombrowser'] = (browser,)
                    with yt_dlp.YoutubeDL(retry_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        if 'entries' in info:
                            return info['entries']
                        return [info]
                except Exception as second_err:
                    msg = str(second_err) or ''
                    # If DPAPI fails or browser cookies aren't accessible, prompt for cookies.txt and retry
                    if 'DPAPI' in msg or messagebox.askyesno('Cookies required', 'Browser cookies unavailable. Select a cookies.txt file to retry?'):
                        if not getattr(self, 'cookies_path', None):
                            self.browse_cookies()
                        if getattr(self, 'cookies_path', None):
                            third_opts = dict(ydl_opts)
                            third_opts['cookiefile'] = self.cookies_path
                            third_opts.pop('cookiesfrombrowser', None)
                            with yt_dlp.YoutubeDL(third_opts) as ydl:
                                info = ydl.extract_info(url, download=False)
                                if 'entries' in info:
                                    return info['entries']
                                return [info]
                    raise second_err from first_err
            raise

    def normalize_url(self, raw_url):
        u = (raw_url or '').strip()
        if not u:
            return u
        # Force https
        if u.lower().startswith('http://'):
            u = 'https://' + u[7:]
        # Replace x.com with twitter.com for broader extractor compatibility
        u = re.sub(r'^(https?://)x\.com/', r'\1twitter.com/', u, flags=re.IGNORECASE)
        return u

    def populate_listbox(self):
        self.listbox.delete(0, 'end')
        self.display_entries = []
        header = f"{'Title':50.50} {'Selection':>16}"
        self.listbox.insert('end', header)
        self.listbox.insert('end', "-" * 80)
        if not self.video_entries:
            self.listbox.insert('end', "No videos found or unsupported site.")
            self.log("No videos found.")
            self.selected_index = None
            return
        for entry in self.video_entries:
            title = entry.get('title', 'No Title')
            for option in self.get_format_options(entry):
                self.display_entries.append({
                    "entry": entry,
                    "format": option["format"],
                    "label": option["label"]
                })
                item = f"{title[:50]:50.50} {option['label']:<16}"
                self.listbox.insert('end', item)
        self.log(f"Found {len(self.video_entries)} video(s).")

    # ──────────────────────────────────────────────────────────
    # Tk callbacks / UI helpers
    # ──────────────────────────────────────────────────────────
    def on_select(self, _):
        sel = self.listbox.curselection()
        if not sel or sel[0] < 2:
            self.selected_index = None
        else:
            self.selected_index = sel[0] - 2
        self.update_button_states()

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_entry.delete(0, 'end')
            self.folder_entry.insert(0, folder)
        self.update_button_states()

    def browse_cookies(self):
        path = filedialog.askopenfilename(title='Select cookies.txt',
                                          filetypes=[('Text files', '*.txt'), ('All files', '*.*')])
        if path:
            self.cookies_path = path
            self.cookies_entry.delete(0, 'end')
            self.cookies_entry.insert(0, path)

    def download_selected(self):
        if self.selected_index is None:
            messagebox.showwarning('Selection Error', 'Select a video/format.')
            return
        if self.selected_index >= len(self.display_entries):
            messagebox.showwarning('Selection Error', 'Invalid selection.')
            return
        folder = self.folder_entry.get().strip()
        if not folder:
            messagebox.showwarning('Folder Error', 'Select a folder first.')
            return
        selection = self.display_entries[self.selected_index]
        entry = selection["entry"]
        url = entry.get('webpage_url', self.url_entry.get().strip())
        fmt_id = selection["format"]
        fmt_label = selection["label"]
        self.last_selected_format = fmt_id
        self.last_selected_label = fmt_label
        self.last_selected_title = entry.get('title', 'Video')
        if not self.ffmpeg_available and '+' in fmt_id:
            if not messagebox.askyesno('ffmpeg missing', 'ffmpeg is required to merge audio/video. Continue anyway?'):
                return
        self.set_progress(0)
        self.log(f"Starting download: {entry.get('title', 'Video')} ({fmt_label})")
        threading.Thread(target=self.download_video,
                         args=(url, fmt_id, folder),
                         daemon=True).start()

    def download_video(self, url, format_id, out_path):
        finished_logged = False
        def hook(d):
            nonlocal finished_logged
            if d['status'] == 'downloading':
                tot = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                self.set_progress(d.get('downloaded_bytes', 0) / tot)
            elif d['status'] == 'finished' and not finished_logged:
                finished_logged = True
                self.set_progress(1)
                self.log('Download complete!')
        opts = {
            'format': format_id,
            'outtmpl': os.path.join(out_path, '%(title)s.%(ext)s'),
            'progress_hooks': [hook],
            'quiet': True,
            'logger': self.ydl_logger
        }
        # If user provided cookies.txt, prefer using it from the start
        if getattr(self, 'cookies_path', None):
            opts['cookiefile'] = self.cookies_path
        # First attempt: normal download
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            return
        except Exception as first_err:
            # For X/Twitter, retry with browser cookies
            if ('twitter.com' in url.lower()) or ('x.com' in url.lower()):
                self.log('[yt-dlp] Download retry with cookies from browser for X/Twitter…')
                browser = None
                try:
                    if hasattr(self, 'dev_defaults'):
                        browser = self.dev_defaults.get('cookies_browser')
                    if not browser:
                        browser = 'edge' if sys.platform.startswith('win') else 'chrome'
                    retry_opts = dict(opts)
                    retry_opts['cookiesfrombrowser'] = (browser,)
                    with yt_dlp.YoutubeDL(retry_opts) as ydl:
                        ydl.download([url])
                    return
                except Exception as second_err:
                    msg = str(second_err) or ''
                    # On DPAPI failure or general browser-cookie failure, prompt for cookies.txt and retry
                    if 'DPAPI' in msg or messagebox.askyesno('Cookies required', 'Browser cookies unavailable. Select a cookies.txt file to retry?'):
                        if not getattr(self, 'cookies_path', None):
                            self.browse_cookies()
                        if getattr(self, 'cookies_path', None):
                            third_opts = dict(opts)
                            third_opts['cookiefile'] = self.cookies_path
                            third_opts.pop('cookiesfrombrowser', None)
                            with yt_dlp.YoutubeDL(third_opts) as ydl:
                                ydl.download([url])
                            return
                    self.log_error("Download", second_err)
                    return
            # Non X/Twitter or both attempts failed
            self.log_error("Download", first_err)

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
        print(f"[DEBUG] Starting Selenium fallback for {page_url}")
        try:
            driver = webdriver.Chrome()  # Assumes chromedriver is in PATH
            driver.get('https://gdcvault.com/login')
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, 'email')))
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, 'password')))
            # Always use dev defaults if enabled
            if hasattr(self, 'dev_defaults') and self.dev_defaults.get('use_defaults'):
                username = self.dev_defaults.get('default_username')
                password = self.dev_defaults.get('default_password')
                print(f"[DEBUG] Using dev defaults for selenium login: {username}")
            driver.find_element(By.NAME, 'email').send_keys(username)
            driver.find_element(By.NAME, 'password').send_keys(password)
            driver.find_element(By.CSS_SELECTOR, 'input[type="submit"][value="LOGIN"]').click()
            time.sleep(5)  # Wait for login
            driver.get(page_url)
            time.sleep(3)
            html = driver.page_source
            driver.quit()
            # Try to extract .m3u8 URL from HTML
            import re
            match = re.search(r'https?://[^"\']+\.m3u8', html)
            if match:
                print(f"[DEBUG] Found .m3u8: {match.group(0)}")
                return match.group(0)
            print("[DEBUG] No .m3u8 found in Selenium fallback.")
            return None
        except Exception as e:
            print(f"[DEBUG] Selenium fallback error: {e}")
            self.log(f"Selenium fallback error: {e}")
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
# ffmpeg helper (stand‑alone)
# ──────────────────────────────────────────────────────────────
def download_with_ffmpeg(m3u8_url, output_file, headers=None):
    header_block = None
    ua = "Mozilla/5.0"
    if headers:
        ua = headers.get("User-Agent", ua)
        header_block = "\\r\\n".join(f"{k}: {v}" for k, v in headers.items()) + "\\r\\n"
    cmd = [
        "ffmpeg", "-y",
        "-headers", header_block if header_block else "",
        "-user_agent", ua,
        "-i", m3u8_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        output_file
    ]
    cmd = [c for c in cmd if c]  # strip blanks
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        pass  # could pipe to a GUI log
    proc.wait()


# ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    root = ctk.CTk()
    app = VideoDownloaderApp(root)
    root.mainloop()
    sys.exit(0)
