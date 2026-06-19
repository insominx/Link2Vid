"""Non-UI fetch orchestration for metadata retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .errors import CookiesRequiredError
from .dev_defaults import dev_domain_for_url
from .extractors import HlsScanResult, extract_embedded_page_videos, scan_direct_media_entries, scan_direct_m3u8

LogFn = Callable[[str], None]
GetVideoInfoFn = Callable[[str, str | None, str | None], list[dict]]


@dataclass
class FetchResults:
    entries: list[dict]
    error: Exception | None = None


@dataclass
class DirectHlsFound:
    result: HlsScanResult
    error: Exception | None = None


@dataclass
class NeedsSelenium:
    error: Exception | None = None


@dataclass
class NeedsCookies:
    error: Exception


@dataclass
class FetchError:
    error: Exception


FetchOutcome = FetchResults | DirectHlsFound | NeedsSelenium | NeedsCookies | FetchError


class VideoFetcher:
    def __init__(
        self,
        *,
        get_video_info: GetVideoInfoFn,
        log: LogFn | None = None,
        dev_defaults: dict | None = None,
    ) -> None:
        self.get_video_info = get_video_info
        self.log = log or (lambda _msg: None)
        self.dev_defaults = dev_defaults or {}

    def fetch(self, url: str, username: str | None = None, password: str | None = None) -> FetchOutcome:
        ytdlp_error: Exception | None = None
        try:
            entries = self.get_video_info(url, username, password)
            return FetchResults(entries=entries)
        except CookiesRequiredError as exc:
            return NeedsCookies(error=exc)
        except Exception as exc:
            ytdlp_error = exc
            if self._needs_cookies(url, exc):
                return NeedsCookies(error=exc)
            if self._is_no_video_error(exc):
                return FetchError(error=exc)

        if dev_domain_for_url(url, self.dev_defaults):
            entries = extract_embedded_page_videos(url, log=self.log)
            if entries:
                return FetchResults(entries=entries, error=ytdlp_error)

        media_entries = scan_direct_media_entries(url, log=self.log)
        if media_entries:
            return FetchResults(entries=media_entries, error=ytdlp_error)

        hls_result = scan_direct_m3u8(url, log=self.log)
        if hls_result:
            return DirectHlsFound(result=hls_result, error=ytdlp_error)

        if ytdlp_error:
            return NeedsSelenium(error=ytdlp_error)

        return FetchError(error=RuntimeError("Fetch failed without details"))

    def _is_no_video_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        signals = (
            "no video could be found",
            "no video found",
            "no media could be found",
            "no video formats found",
        )
        return any(token in message for token in signals)

    def _needs_cookies(self, url: str, exc: Exception) -> bool:
        message = str(exc).lower()
        signals = (
            "account",
            "dpapi",
            "cookie",
            "cookies",
            "consent",
            "sign in",
            "signin",
            "login",
            "age-restricted",
            "age restricted",
            "age verification",
            "403",
            "forbidden",
            "bot",
            "verify",
            "verification",
        )
        return any(token in message for token in signals)
