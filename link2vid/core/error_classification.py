"""Error classification helpers for user-facing hints."""

from __future__ import annotations


def _has_impersonation_signal(msg: str) -> bool:
    return any(token in msg for token in (
        "impersonation",
        "impersonate target",
        "no impersonate target",
    ))


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
    if any(token in msg for token in ("no transcript", "no subtitles", "no caption", "no captions")):
        return "no transcript", " (Hint: this video may not expose transcript/caption tracks.)"
    if any(token in msg for token in ("ffmpeg", "ffprobe", "avconv", "merge", "mux")):
        return "ffmpeg", " (Hint: install ffmpeg and ensure it's on PATH.)"
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
    if _has_impersonation_signal(msg):
        return "extractor drift/SABR", " (Hint: install yt-dlp impersonation dependencies or update yt-dlp.)"
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
    return "unknown", ""


def get_error_guidance(err_text: str) -> tuple[str, str, str] | None:
    msg = (err_text or "").lower()
    reason, _hint = classify_error(err_text)

    if reason == "js runtime":
        return (
            "js runtime",
            "JavaScript runtime required",
            "yt-dlp needs a JavaScript runtime (deno/node/bun) for some YouTube downloads.\n"
            "Install one and ensure it is on PATH. Some cases also require the yt-dlp\n"
            "challenge solver scripts (see the yt-dlp EJS wiki) before retrying.",
        )

    if reason == "network/rate-limit":
        return (
            "network/rate-limit",
            "Rate limited by site",
            "The site is temporarily rejecting requests (HTTP 429 / rate limit).\n\n"
            "Try again after waiting a bit, switch networks if possible, and consider\n"
            "using browser cookies or cookies.txt for YouTube. If this keeps happening,\n"
            "copy diagnostics and update yt-dlp before retrying.",
        )

    if reason == "extractor drift/SABR" and _has_impersonation_signal(msg):
        return (
            "extractor drift/SABR:impersonation",
            "yt-dlp impersonation support needed",
            "yt-dlp requested an impersonation target, but none is available.\n\n"
            "Install the required yt-dlp impersonation dependencies, or update yt-dlp\n"
            "to a build/environment that supports impersonation, then retry.",
        )

    return None
