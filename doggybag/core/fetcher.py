"""Non-UI fetch orchestration for metadata retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import requests

from .errors import CookiesRequiredError
from .dev_defaults import dev_domain_for_url
from .extractors import HlsScanResult, extract_embedded_page_videos, scan_direct_media_entries, scan_direct_m3u8
from .gdcvault import GdcVaultLoginError, GdcVaultLoginRequired, extract_gdcvault_entries, is_gdcvault_url
from .udio import extract_udio_entries, is_udio_url

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
class NeedsLogin:
    """Site login is plain HTTP; retry fetch() with credentials instead of Selenium."""

    error: Exception


@dataclass
class FetchError:
    error: Exception


FetchOutcome = FetchResults | DirectHlsFound | NeedsSelenium | NeedsCookies | NeedsLogin | FetchError


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
        if is_udio_url(url):
            try:
                udio_entries = extract_udio_entries(url, log=self.log)
            except Exception as exc:
                return FetchError(error=exc)
            if udio_entries:
                return FetchResults(entries=udio_entries)
            return FetchError(
                error=RuntimeError(
                    "No public Udio audio found. Paste a public /songs/ URL. "
                    "Udio sign-in uses an email link, which Doggybag cannot automate."
                )
            )

        if is_gdcvault_url(url):
            return self._fetch_gdcvault(url, username, password)

        ytdlp_error: Exception | None = None
        try:
            entries = self.get_video_info(url, username, password)
            return FetchResults(entries=entries)
        except CookiesRequiredError as exc:
            if self._should_try_selenium_for_auth(url, exc, username, password):
                return NeedsSelenium(error=exc)
            return NeedsCookies(error=exc)
        except Exception as exc:
            ytdlp_error = exc
            if self._needs_cookies(url, exc):
                if self._should_try_selenium_for_auth(url, exc, username, password):
                    return NeedsSelenium(error=exc)
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

    def _fetch_gdcvault(self, url: str, username: str | None, password: str | None) -> FetchOutcome:
        try:
            entries = extract_gdcvault_entries(url, username, password, log=self.log)
        except GdcVaultLoginRequired as exc:
            return NeedsLogin(error=exc)
        except (GdcVaultLoginError, requests.RequestException) as exc:
            return FetchError(error=exc)
        if entries:
            return FetchResults(entries=entries)

        self.log("[GDC Vault] No Blazestreaming player on this page; trying yt-dlp for older talk players.")
        try:
            return FetchResults(entries=self.get_video_info(url, username, password))
        except Exception as exc:
            return FetchError(error=RuntimeError(f"No playable GDC Vault video found on this page ({exc})."))

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

    def _should_try_selenium_for_auth(
        self,
        url: str,
        exc: Exception,
        username: str | None,
        password: str | None,
    ) -> bool:
        if self._is_login_redirect_error(exc):
            return True
        if username and password:
            return True
        return bool(dev_domain_for_url(url, self.dev_defaults))

    def _is_login_redirect_error(self, exc: Exception) -> bool:
        for message in self._exception_messages(exc):
            if "unsupported url" not in message:
                continue
            if any(token in message for token in ("/login", "/users/login", "/sign_in", "/sign-in")):
                return True
        return False

    def _exception_messages(self, exc: Exception) -> list[str]:
        messages: list[str] = []
        seen: set[int] = set()
        pending: list[Exception | None] = [exc]
        while pending:
            current = pending.pop()
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))
            messages.append(str(current).lower())
            original = getattr(current, "original", None)
            if isinstance(original, Exception):
                pending.append(original)
            pending.append(current.__cause__)
            pending.append(current.__context__)
        return messages
