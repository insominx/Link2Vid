"""Core utilities for Link2Vid."""

from .downloader import DownloadManager
from .diagnostics import build_diagnostics
from .errors import CookiesRequiredError, NoTranscriptAvailableError
from .error_classification import classify_error, get_error_guidance
from .extractors import extract_linkedin_videos, scan_direct_m3u8
from .fetcher import (
    DirectHlsFound,
    FetchError,
    FetchOutcome,
    FetchResults,
    NeedsCookies,
    NeedsSelenium,
    VideoFetcher,
)
from .helpers import (
    download_with_ffmpeg,
    get_format_options,
    get_yt_dlp_version,
    normalize_url,
)
from .selenium_fallback import selenium_fetch_m3u8

__all__ = [
    "DownloadManager",
    "CookiesRequiredError",
    "NoTranscriptAvailableError",
    "build_diagnostics",
    "classify_error",
    "get_error_guidance",
    "download_with_ffmpeg",
    "extract_linkedin_videos",
    "get_format_options",
    "get_yt_dlp_version",
    "normalize_url",
    "scan_direct_m3u8",
    "selenium_fetch_m3u8",
    "DirectHlsFound",
    "FetchError",
    "FetchOutcome",
    "FetchResults",
    "NeedsCookies",
    "NeedsSelenium",
    "VideoFetcher",
]
