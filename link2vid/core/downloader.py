"""Download/extraction logic extracted from the UI layer."""

from __future__ import annotations

from typing import Callable

from .errors import CookiesRequiredError
import sys
import subprocess
import re
import shutil
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
    ) -> None:
        self.ydl_logger = ydl_logger
        self.log = log or (lambda _msg: None)
        self.log_error = log_error or (lambda _stage, _err: None)
        self.dev_defaults = dev_defaults or {}
        self.get_cookies_path = get_cookies_path or (lambda: None)
        self.last_cookies_mode = "none"
        self.last_cookies_browser = None
        self.last_js_runtime = None
        self.last_js_runtime_path = None
        self.last_remote_components = []

    def _browser_name(self) -> str:
        browser = None
        if self.dev_defaults:
            browser = self.dev_defaults.get("cookies_browser")
        if not browser:
            browser = "edge" if sys.platform.startswith("win") else "chrome"
        return browser

    def _running_browsers(self) -> list[str]:
        candidates: list[str] = []
        output = ""
        if sys.platform.startswith("win"):
            try:
                result = subprocess.run(["tasklist"], capture_output=True, text=True, check=False)
                output = result.stdout.lower()
            except Exception:
                return []
            process_map = {
                "msedge.exe": "edge",
                "brave.exe": "brave",
                "chrome.exe": "chrome",
                "firefox.exe": "firefox",
            }
        else:
            try:
                result = subprocess.run(["ps", "-A"], capture_output=True, text=True, check=False)
                output = result.stdout.lower()
            except Exception:
                return []
            process_map = {
                "microsoft-edge": "edge",
                "msedge": "edge",
                "brave-browser": "brave",
                "brave": "brave",
                "chrome": "chrome",
                "chromium": "chrome",
                "firefox": "firefox",
            }
        for process, browser in process_map.items():
            if process in output:
                candidates.append(browser)
        return candidates

    def _reset_cookie_state(self) -> None:
        self.last_cookies_mode = "none"
        self.last_cookies_browser = None

    def _apply_cookies(self, opts: dict) -> None:
        cookies_path = self.get_cookies_path()
        if cookies_path:
            opts["cookiefile"] = cookies_path
            self.last_cookies_mode = "cookies.txt"

    def _mark_browser_cookies(self, browser: str) -> None:
        self.last_cookies_mode = "browser"
        self.last_cookies_browser = browser

    def _is_x_url(self, url: str) -> bool:
        lowered = url.lower()
        return "twitter.com" in lowered or "x.com" in lowered

    def _is_youtube_url(self, url: str) -> bool:
        lowered = url.lower()
        return any(token in lowered for token in ("youtube.com", "youtu.be", "youtube-nocookie.com"))

    def _is_cookie_error(self, err: Exception) -> bool:
        message = str(err).lower()
        signals = (
            "account",
            "cookie",
            "cookies",
            "consent",
            "sign in",
            "signin",
            "login",
            "age",
            "403",
            "forbidden",
            "bot",
            "verify",
            "verification",
        )
        return any(token in message for token in signals)

    def _browser_candidates(self) -> list[str]:
        ordered: list[str] = []
        seen = set()

        preferred = None
        if self.dev_defaults:
            preferred = self.dev_defaults.get("cookies_browser")
        if preferred and preferred not in seen:
            seen.add(preferred)
            ordered.append(preferred)

        for browser in self._running_browsers():
            if browser not in seen:
                seen.add(browser)
                ordered.append(browser)

        fallback = ["edge", "chrome", "brave", "firefox"]
        for browser in fallback:
            if browser not in seen:
                seen.add(browser)
                ordered.append(browser)

        return ordered

    def _select_js_runtime(self) -> tuple[str | None, str | None]:
        candidates = [
            ("deno", "deno"),
            ("node", "node"),
            ("bun", "bun"),
            ("quickjs", "qjs"),
        ]
        for runtime, exe in candidates:
            path = shutil.which(exe)
            if path:
                return runtime, path
        return None, None

    def _apply_js_runtime_opts(self, opts: dict) -> None:
        runtime, path = self._select_js_runtime()
        self.last_js_runtime = runtime
        self.last_js_runtime_path = path
        if runtime:
            opts["js_runtimes"] = {runtime: {"path": path} if path else {}}
            components = ["ejs:github"]
            if runtime in ("deno", "bun"):
                components.append("ejs:npm")
            opts["remote_components"] = components
            self.last_remote_components = components
            self.log(f"[yt-dlp] JS runtime selected: {runtime} ({path})")
            self.log(f"[yt-dlp] EJS remote components enabled: {', '.join(components)}")
            try:
                import yt_dlp_ejs
                version = getattr(yt_dlp_ejs, "__version__", "unknown")
                self.log(f"[yt-dlp] EJS package detected: yt-dlp-ejs {version}")
            except Exception:
                self.log("[yt-dlp] EJS package not detected; relying on remote components.")
        else:
            self.last_remote_components = []
            self.log("[yt-dlp] No JS runtime found; EJS challenge solver will be unavailable.")

    def _strip_ansi(self, text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", text or "")

    def _format_exception(self, err: Exception) -> str:
        text = self._strip_ansi(str(err) or "")
        if len(text) > 240:
            text = text[:237] + "..."
        if text:
            return f"{type(err).__name__}: {text}"
        return type(err).__name__

    def _cookie_failure_hint(self, err: Exception) -> str | None:
        msg = self._strip_ansi(str(err) or "").lower()
        if "dpapi" in msg:
            return "Hint: Windows DPAPI decrypt failed. Close the browser and retry, or use cookies.txt."
        if "could not copy chrome cookie database" in msg:
            return "Hint: Browser cookie database is locked. Fully close the browser (including background processes) and retry."
        if "could not find firefox cookies database" in msg:
            return "Hint: Firefox profile not found on this machine."
        return None

    def _should_try_browser_cookies(self, url: str, err: Exception) -> bool:
        if self._is_x_url(url):
            return True
        if self._is_youtube_url(url):
            return self._is_cookie_error(err)
        return False

    def get_video_info(self, url: str, username: str | None = None, password: str | None = None):
        self._reset_cookie_state()
        ydl_opts = {"quiet": True, "skip_download": True, "logger": self.ydl_logger}
        if username and password:
            ydl_opts["username"] = username
            ydl_opts["password"] = password
        self._apply_cookies(ydl_opts)
        self._apply_js_runtime_opts(ydl_opts)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if "entries" in info:
                    return info["entries"]
                return [info]
        except Exception as first_err:
            if self._should_try_browser_cookies(url, first_err):
                site_label = "X/Twitter" if self._is_x_url(url) else "YouTube"
                last_err = first_err
                candidates = self._browser_candidates()
                self.log(f"[yt-dlp] Browser cookie candidates: {', '.join(candidates)}")
                for browser in candidates:
                    self.log(f"[yt-dlp] Retry with cookies from browser ({browser}) for {site_label}…")
                    try:
                        retry_opts = dict(ydl_opts)
                        retry_opts.pop("cookiefile", None)
                        retry_opts["cookiesfrombrowser"] = (browser,)
                        self._mark_browser_cookies(browser)
                        with yt_dlp.YoutubeDL(retry_opts) as ydl:
                            info = ydl.extract_info(url, download=False)
                            if "entries" in info:
                                return info["entries"]
                            return [info]
                    except Exception as retry_err:
                        self.log(f"[yt-dlp] Browser cookies ({browser}) failed: {self._format_exception(retry_err)}")
                        hint = self._cookie_failure_hint(retry_err)
                        if hint:
                            self.log(f"[yt-dlp] {hint}")
                        last_err = retry_err
                        continue
                self.log("[yt-dlp] All browser cookie attempts failed; falling back to cookies.txt prompt.")
                raise CookiesRequiredError(original=last_err) from first_err
            raise

    def download(self, url: str, format_id: str, out_path: str, progress_hook: Callable | None = None) -> bool:
        self._reset_cookie_state()
        opts = {
            "format": format_id,
            "outtmpl": out_path,
            "progress_hooks": [progress_hook] if progress_hook else [],
            "quiet": True,
            "logger": self.ydl_logger,
        }
        self._apply_cookies(opts)
        self._apply_js_runtime_opts(opts)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            return True
        except Exception as first_err:
            if self._should_try_browser_cookies(url, first_err):
                site_label = "X/Twitter" if self._is_x_url(url) else "YouTube"
                last_err = first_err
                candidates = self._browser_candidates()
                self.log(f"[yt-dlp] Browser cookie candidates: {', '.join(candidates)}")
                for browser in candidates:
                    self.log(f"[yt-dlp] Download retry with cookies from browser ({browser}) for {site_label}…")
                    try:
                        retry_opts = dict(opts)
                        retry_opts.pop("cookiefile", None)
                        retry_opts["cookiesfrombrowser"] = (browser,)
                        self._mark_browser_cookies(browser)
                        with yt_dlp.YoutubeDL(retry_opts) as ydl:
                            ydl.download([url])
                        return True
                    except Exception as retry_err:
                        self.log(f"[yt-dlp] Browser cookies ({browser}) failed: {self._format_exception(retry_err)}")
                        hint = self._cookie_failure_hint(retry_err)
                        if hint:
                            self.log(f"[yt-dlp] {hint}")
                        last_err = retry_err
                        continue
                self.log("[yt-dlp] All browser cookie attempts failed; falling back to cookies.txt prompt.")
                raise CookiesRequiredError(original=last_err) from first_err
            self.log_error("Download", first_err)
            return False
