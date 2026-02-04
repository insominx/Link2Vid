"""Core error types for Link2Vid."""

from __future__ import annotations


class CookiesRequiredError(RuntimeError):
    """Raised when a cookies.txt file is required to proceed."""

    def __init__(self, message: str = "Cookies are required to access this content.", *, original: Exception | None = None) -> None:
        super().__init__(message)
        self.original = original
