"""Core utilities for Link2Vid."""

from .downloader import DownloadManager
from .diagnostics import build_diagnostics
from .errors import CookiesRequiredError, NoTranscriptAvailableError
from .error_classification import classify_error, get_error_guidance
from .extractors import extract_embedded_page_videos, scan_direct_m3u8, scan_direct_media_entries, build_media_entries
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
    sanitize_filename,
    unique_output_path,
    url_from_clipboard_text,
)
from .selenium_fallback import SeleniumMediaResult, selenium_fetch_m3u8, selenium_fetch_media_entries

__all__ = [
    "DownloadManager",
    "CookiesRequiredError",
    "NoTranscriptAvailableError",
    "build_diagnostics",
    "classify_error",
    "get_error_guidance",
    "download_with_ffmpeg",
    "extract_embedded_page_videos",
    "get_format_options",
    "get_yt_dlp_version",
    "normalize_url",
    "sanitize_filename",
    "unique_output_path",
    "url_from_clipboard_text",
    "scan_direct_m3u8",
    "scan_direct_media_entries",
    "build_media_entries",
    "selenium_fetch_m3u8",
    "selenium_fetch_media_entries",
    "SeleniumMediaResult",
    "DirectHlsFound",
    "FetchError",
    "FetchOutcome",
    "FetchResults",
    "NeedsCookies",
    "NeedsSelenium",
    "VideoFetcher",
]
