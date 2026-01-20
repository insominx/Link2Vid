"""Download/extraction logic extracted from the UI layer."""

from __future__ import annotations

from typing import Callable
import sys
import yt_dlp


class DownloadManager:
    def __init__(
        self,
        *,
        ydl_logger,
        log: Callable[[str], None] | None = None,
        log_error: Callable[[str, Exception], None] | None = None,
        dev_defaults: dict | None = None,
        get_cookies_path: Callable[[], str | None] | None = None,
        select_cookies: Callable[[], None] | None = None,
        confirm: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.ydl_logger = ydl_logger
        self.log = log or (lambda _msg: None)
        self.log_error = log_error or (lambda _stage, _err: None)
        self.dev_defaults = dev_defaults or {}
        self.get_cookies_path = get_cookies_path or (lambda: None)
        self.select_cookies = select_cookies or (lambda: None)
        self.confirm = confirm or (lambda _title, _message: False)

    def _browser_name(self) -> str:
        browser = None
        if self.dev_defaults:
            browser = self.dev_defaults.get("cookies_browser")
        if not browser:
            browser = "edge" if sys.platform.startswith("win") else "chrome"
        return browser

    def _apply_cookies(self, opts: dict) -> None:
        cookies_path = self.get_cookies_path()
        if cookies_path:
            opts["cookiefile"] = cookies_path

    def get_video_info(self, url: str, username: str | None = None, password: str | None = None):
        ydl_opts = {"quiet": True, "skip_download": True, "logger": self.ydl_logger}
        if username and password:
            ydl_opts["username"] = username
            ydl_opts["password"] = password
        self._apply_cookies(ydl_opts)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if "entries" in info:
                    return info["entries"]
                return [info]
        except Exception as first_err:
            if ("twitter.com" in url.lower()) or ("x.com" in url.lower()):
                self.log("[yt-dlp] Retry with cookies from browser for X/Twitter…")
                browser = self._browser_name()
                try:
                    retry_opts = dict(ydl_opts)
                    retry_opts["cookiesfrombrowser"] = (browser,)
                    with yt_dlp.YoutubeDL(retry_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        if "entries" in info:
                            return info["entries"]
                        return [info]
                except Exception as second_err:
                    msg = str(second_err) or ""
                    if "DPAPI" in msg or self.confirm(
                        "Cookies required",
                        "Browser cookies unavailable. Select a cookies.txt file to retry?",
                    ):
                        if not self.get_cookies_path():
                            self.select_cookies()
                        if self.get_cookies_path():
                            third_opts = dict(ydl_opts)
                            third_opts["cookiefile"] = self.get_cookies_path()
                            third_opts.pop("cookiesfrombrowser", None)
                            with yt_dlp.YoutubeDL(third_opts) as ydl:
                                info = ydl.extract_info(url, download=False)
                                if "entries" in info:
                                    return info["entries"]
                                return [info]
                    raise second_err from first_err
            raise

    def download(self, url: str, format_id: str, out_path: str, progress_hook: Callable | None = None) -> bool:
        opts = {
            "format": format_id,
            "outtmpl": out_path,
            "progress_hooks": [progress_hook] if progress_hook else [],
            "quiet": True,
            "logger": self.ydl_logger,
        }
        self._apply_cookies(opts)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            return True
        except Exception as first_err:
            if ("twitter.com" in url.lower()) or ("x.com" in url.lower()):
                self.log("[yt-dlp] Download retry with cookies from browser for X/Twitter…")
                browser = self._browser_name()
                try:
                    retry_opts = dict(opts)
                    retry_opts["cookiesfrombrowser"] = (browser,)
                    with yt_dlp.YoutubeDL(retry_opts) as ydl:
                        ydl.download([url])
                    return True
                except Exception as second_err:
                    msg = str(second_err) or ""
                    if "DPAPI" in msg or self.confirm(
                        "Cookies required",
                        "Browser cookies unavailable. Select a cookies.txt file to retry?",
                    ):
                        if not self.get_cookies_path():
                            self.select_cookies()
                        if self.get_cookies_path():
                            third_opts = dict(opts)
                            third_opts["cookiefile"] = self.get_cookies_path()
                            third_opts.pop("cookiesfrombrowser", None)
                            with yt_dlp.YoutubeDL(third_opts) as ydl:
                                ydl.download([url])
                            return True
                    self.log_error("Download", second_err)
                    return False
            self.log_error("Download", first_err)
            return False
