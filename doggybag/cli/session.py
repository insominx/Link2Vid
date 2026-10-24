"""Wiring for one CLI run: options, core services, and a prompter that never prompts."""

from __future__ import annotations

import functools
import os
import shutil
from dataclasses import dataclass
from typing import Callable

from ..app.workflow import FetchResolution, load_dev_defaults, resolve_fetch
from ..core.cookie_prefs import (
    cookies_browser_label,
    load_preferred_cookies_browser,
    load_preferred_cookies_path,
    load_user_settings,
)
from ..core.dev_defaults import dev_credentials_for_url, dev_domain_for_url
from ..core.downloader import DownloadManager
from ..core.error_classification import classify_error
from ..core.fetcher import VideoFetcher
from .output import Reporter


@dataclass
class CliOptions:
    out_dir: str
    cookies_browser: str | None = None
    cookies_file: str | None = None
    username: str | None = None
    password: str | None = None
    allow_browser: bool = False
    flat: bool = False
    format_fallback: str = "best"  # "best" | "fail"
    if_no_ffmpeg: str = "fallback"  # "fallback" | "continue" | "fail"


def warning(code: str, message: str) -> dict:
    return {"code": code, "message": message}


class FlagPrompter:
    """Answers workflow questions from flags; records what an agent must change instead of asking."""

    def __init__(self, options: CliOptions, *, dev_defaults: dict, log: Callable[[str], None]) -> None:
        self.options = options
        self.dev_defaults = dev_defaults
        self.log = log
        self.warnings: list[dict] = []
        self.declined: list[str] = []

    def cookie_retry(self) -> bool:
        # Cookie flags were applied before the first attempt; nothing changes on a retry.
        self.declined.append("cookies")
        return False

    def format_fallback(self, *, extract_audio: bool) -> bool:
        if self.options.format_fallback == "best":
            target = "extracting MP3 from the best available file" if extract_audio else "the best available format"
            self.warnings.append(
                warning("format_fallback", f"Requested format was unavailable; retried with {target}.")
            )
            return True
        self.declined.append("format")
        return False

    def ffmpeg_missing(self, fmt_label: str) -> str:
        policy = self.options.if_no_ffmpeg
        if policy == "fallback":
            self.warnings.append(
                warning(
                    "ffmpeg_missing_fallback",
                    f"ffmpeg is missing, so '{fmt_label}' cannot be merged; used best (single file).",
                )
            )
            return "fallback"
        if policy == "continue":
            self.warnings.append(
                warning(
                    "ffmpeg_missing_continue",
                    f"ffmpeg is missing; '{fmt_label}' may fail or save video without audio.",
                )
            )
            return "continue"
        self.declined.append("ffmpeg")
        return "cancel"

    def credentials(self, url: str, *, browser_automation: bool):
        username = self.options.username or os.environ.get("DOGGYBAG_USERNAME")
        password = self.options.password or os.environ.get("DOGGYBAG_PASSWORD")
        if username and password:
            return username, password
        username, password = dev_credentials_for_url(url, self.dev_defaults)
        if username and password:
            self.log(f"[dev] Using developer.json credentials for {dev_domain_for_url(url, self.dev_defaults)}.")
            return username, password
        self.declined.append("credentials")
        return None, None

    def allow_browser_automation(self) -> bool:
        if self.options.allow_browser:
            self.warnings.append(
                warning("browser_automation", "Opened Chrome to scrape the page (--allow-browser).")
            )
            return True
        self.declined.append("browser")
        return False

    def drain(self) -> tuple[list[dict], list[str]]:
        warnings, declined = self.warnings, self.declined
        self.warnings, self.declined = [], []
        return warnings, declined


class _YdlLogger:
    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log

    def debug(self, _msg):
        return None

    def warning(self, msg):
        self._log(f"[yt-dlp] {msg}")

    def error(self, msg):
        self._log(f"[yt-dlp] {msg}")


class Session:
    """Core services for one CLI run. Reads saved preferences; never writes them."""

    def __init__(
        self,
        options: CliOptions,
        reporter: Reporter,
        *,
        manager: DownloadManager | None = None,
        fetcher: VideoFetcher | None = None,
        whisper_service=None,
        audio_resolver=None,
        dev_defaults: dict | None = None,
        settings: dict | None = None,
        selenium_fetch: Callable[[str, str, str], list[dict]] | None = None,
    ) -> None:
        self.options = options
        self.reporter = reporter
        self.dev_defaults = dev_defaults if dev_defaults is not None else load_dev_defaults()
        settings = settings if settings is not None else load_user_settings()
        self.last_error = None
        self.last_error_reason = None

        # Cookie sources, in the GUI's precedence; only an explicit flag forces
        # browser cookies onto the first attempt.
        if options.cookies_file:
            self.cookies_path, path_source = options.cookies_file, "flag"
        else:
            self.cookies_path = self.dev_defaults.get("cookies_path") or load_preferred_cookies_path(
                settings=settings, dev_defaults=self.dev_defaults
            )
            path_source = "developer.json" if self.dev_defaults.get("cookies_path") else "settings"
        if options.cookies_browser:
            self.cookies_browser, browser_source = options.cookies_browser, "flag"
        else:
            self.cookies_browser = load_preferred_cookies_browser(
                settings=settings, dev_defaults=self.dev_defaults
            )
            browser_source = "settings" if (settings or {}).get("cookies_browser") else "developer.json"
        if self.cookies_path:
            self.cookies_source = path_source
        elif self.cookies_browser:
            self.cookies_source = browser_source
        else:
            self.cookies_source = "none"

        self.prompter = FlagPrompter(options, dev_defaults=self.dev_defaults, log=self.log)
        self.manager = manager or DownloadManager(
            ydl_logger=_YdlLogger(self.log),
            log=self.log,
            log_error=self.log_error,
            dev_defaults=self.dev_defaults,
            get_cookies_path=lambda: self.cookies_path,
            get_cookies_browser=lambda: self.cookies_browser,
        )
        self.manager.force_browser_cookies = bool(options.cookies_browser)
        if fetcher is None:
            get_info = self.manager.get_video_info
            if options.flat:
                get_info = functools.partial(self.manager.get_video_info, flat=True)
            fetcher = VideoFetcher(get_video_info=get_info, log=self.log, dev_defaults=self.dev_defaults)
        self.fetcher = fetcher
        self._whisper_service = whisper_service
        self._audio_resolver = audio_resolver
        self.selenium_fetch = selenium_fetch
        self.ffmpeg_path = shutil.which("ffmpeg")
        self.ffprobe_path = shutil.which("ffprobe")

    # Logging ---------------------------------------------------------------
    def log(self, message: str) -> None:
        self.reporter.log(message)

    def log_error(self, stage: str, err: Exception) -> None:
        err_text = str(err) or "Unknown error"
        reason, hint = classify_error(err_text)
        self.last_error = err
        self.last_error_reason = reason
        reason_text = f" (Reason: {reason})" if reason else ""
        self.log(f"[{stage}] {type(err).__name__}: {err_text}{reason_text}{hint}")

    # Services --------------------------------------------------------------
    @property
    def ffmpeg_available(self) -> bool:
        return bool(self.ffmpeg_path)

    def whisper_service(self):
        if self._whisper_service is None:
            from ..core.whisper_transcription import WhisperService

            self._whisper_service = WhisperService()
        return self._whisper_service

    def audio_resolver(self):
        if self._audio_resolver is None:
            from ..core.audio_source import AudioSourceResolver

            self._audio_resolver = AudioSourceResolver(download_manager=self.manager)
        return self._audio_resolver

    def fetch(self, url: str) -> FetchResolution:
        return resolve_fetch(
            url,
            fetcher=self.fetcher,
            prompter=self.prompter,
            log=self.log,
            log_error=self.log_error,
            dev_defaults=self.dev_defaults,
            selenium_fetch=self.selenium_fetch,
        )

    def cookies_report(self) -> dict:
        return {
            "mode": getattr(self.manager, "last_cookies_mode", "none") or "none",
            "browser": getattr(self.manager, "last_cookies_browser", None),
            "source": self.cookies_source,
            "configured": cookies_browser_label(self.cookies_browser) if self.cookies_browser else None,
        }


__all__ = ["CliOptions", "FlagPrompter", "Session", "warning"]
