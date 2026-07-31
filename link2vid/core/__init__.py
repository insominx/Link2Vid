"""Core utilities for Link2Vid."""

from .downloader import DownloadManager
from .diagnostics import (
    WhisperDiagnosticsInput,
    build_diagnostics,
    sanitize_whisper_diagnostic_scalar,
    sanitize_whisper_error_reason,
)
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
    entry_watch_url,
    get_format_options,
    get_yt_dlp_version,
    normalize_url,
    sanitize_filename,
    unique_output_path,
    url_from_clipboard_text,
)
from .selenium_fallback import SeleniumMediaResult, selenium_fetch_m3u8, selenium_fetch_media_entries
from .audio_source import (
    AudioAcquisitionError,
    AudioSourceResolver,
    SourceKind,
    SourceLease,
)
from .whisper_transcription import (
    GpuInitializationUnavailable,
    TranscriptionRequest,
    TranscriptionResult,
    VerifiedMedia,
    WhisperPhase,
    WhisperRunSnapshot,
    WhisperService,
)

__all__ = [
    "DownloadManager",
    "CookiesRequiredError",
    "NoTranscriptAvailableError",
    "WhisperDiagnosticsInput",
    "build_diagnostics",
    "sanitize_whisper_diagnostic_scalar",
    "sanitize_whisper_error_reason",
    "classify_error",
    "get_error_guidance",
    "download_with_ffmpeg",
    "entry_watch_url",
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
    "VerifiedMedia",
    "WhisperPhase",
    "TranscriptionRequest",
    "TranscriptionResult",
    "WhisperRunSnapshot",
    "GpuInitializationUnavailable",
    "WhisperService",
    "SourceKind",
    "SourceLease",
    "AudioAcquisitionError",
    "AudioSourceResolver",
]
