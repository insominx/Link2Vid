"""Core error types for Doggybag."""

from __future__ import annotations


class CookiesRequiredError(RuntimeError):
    """Raised when browser cookies are required to proceed."""

    def __init__(self, message: str = "Cookies are required to access this content.", *, original: Exception | None = None) -> None:
        super().__init__(message)
        self.original = original


class NoTranscriptAvailableError(RuntimeError):
    """Raised when a video does not expose subtitles/captions that can be downloaded."""


class DownloadStalledError(RuntimeError):
    """Raised from a progress hook when a download crawls below the stall threshold."""

    def __init__(self, *, rate: float, window: float) -> None:
        super().__init__(
            f"Download stalled: {rate / 1024:.1f} KiB/s over the last {window:.0f} s"
        )
        self.rate = rate
        self.window = window
