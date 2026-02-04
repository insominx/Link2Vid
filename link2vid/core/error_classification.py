"""Error classification helpers for user-facing hints."""

from __future__ import annotations


def classify_error(err_text: str) -> tuple[str, str]:
    msg = (err_text or "").lower()
    if any(token in msg for token in ("challenge solving failed", "n challenge", "challenge solver", "ejs", "sig function possibilities", "[jsc]")):
        return "js runtime", " (Hint: update yt-dlp/yt-dlp-ejs and ensure a JS runtime; see the yt-dlp EJS wiki.)"
    if any(token in msg for token in ("javascript runtime", "js runtime")):
        return "js runtime", " (Hint: install deno/node/bun and ensure it's on PATH.)"
    if "requested format is not available" in msg:
        if "youtube" in msg:
            return "format unavailable", " (Hint: re-fetch formats; if YouTube shows only images, install EJS scripts.)"
        return "format unavailable", " (Hint: re-fetch formats or try Best (single file).)"
    if any(token in msg for token in ("only images are available", "no video formats")):
        return "format unavailable", " (Hint: install EJS scripts or re-fetch formats.)"
    if any(token in msg for token in ("ffmpeg", "ffprobe", "avconv", "merge", "mux")):
        return "ffmpeg", " (Hint: install ffmpeg and ensure it's on PATH.)"
    if any(token in msg for token in (
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
        "account",
        "verify",
        "verification",
    )):
        return "cookies/auth", " (Hint: try cookies.txt or browser cookies.)"
    if any(token in msg for token in (
        "signature",
        "cipher",
        "extractor",
        "unable to extract",
        "sabr",
        "nsig",
        "player response",
        "po token",
    )):
        return "extractor drift/SABR", " (Hint: try updating yt-dlp.)"
    if any(token in msg for token in (
        "429",
        "too many requests",
        "rate limit",
        "throttle",
        "timeout",
        "timed out",
        "connection",
        "network",
        "ssl",
        "http error 5",
        "http error 429",
        "http error 503",
        "http error 502",
        "http error 500",
    )):
        return "network/rate-limit", " (Hint: check your network or wait and retry.)"
    return "unknown", ""
