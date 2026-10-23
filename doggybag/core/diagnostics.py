"""Diagnostics assembly helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable
import unicodedata


WHISPER_DIAGNOSTIC_MAX_LENGTH = 240
_REDACTED = "[REDACTED]"
_REDACTED_PATH = "[REDACTED PATH]"
_GENERIC_WHISPER_ERROR = (
    "Whisper transcription failed; see the debug log for details"
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_COOKIE_HEADER_RE = re.compile(
    r"\b(?P<header>set-cookie|cookie)\s*:\s*.*$",
    re.IGNORECASE,
)
_KEY_VALUE_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_.-])
    (?P<key>[A-Za-z][A-Za-z0-9_.-]{0,63})
    \s*[:=]\s*
    (?P<value>
        (?:bearer|basic)\s+[^\s,;]+|
        "[^"]*"|
        '[^']*'|
        [^\s,;]+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_STACK_BANNER_RE = re.compile(
    r"""
    (?:^|:\s*)(?:
        traceback\b|
        stack(?:\s+trace)?\b|
        exception\s+stack\b|
        file\s+["'].+?["']\s*,?\s*line\s+\d+\b|
        at\s+.+?[/\\].+?:\d+(?::\d+)?\)?$
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_QUOTED_ABSOLUTE_PATH_RE = re.compile(
    r"""
    (?P<quote>["'])
    (?:
        [A-Za-z]:[\\/]|
        \\\\|
        /(?:tmp|var/tmp|private/var/(?:tmp|folders)|home|Users)/
    )
    .*?
    (?P=quote)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\).*$",
    re.IGNORECASE,
)
_POSIX_PRIVATE_PATH_RE = re.compile(
    r"(?<!\w)/(?:tmp|var/tmp|private/var/(?:tmp|folders)|home/[^/\s]+|Users/[^/\s]+)"
    r".*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WhisperDiagnosticsInput:
    """UI-independent Whisper fields for Copy Diagnostics.

    Core never imports card/window types. Callers pass only plain values
    derived from an immutable ``WhisperRunSnapshot`` after selection checks.
    """

    model_name: str | None = None
    phase: str | None = None
    cache_root: str | None = None
    source_kind: str | None = None
    effective_device: str | None = None
    fallback_used: bool | None = None
    error_reason: str | None = None


def _stringify(value: object | None) -> str:
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _normalize_whitespace_and_controls(value: object | None) -> str:
    """Collapse whitespace and Unicode control/format characters to spaces."""
    chars = []
    for char in _stringify(value):
        if char.isspace() or unicodedata.category(char).startswith("C"):
            chars.append(" ")
        else:
            chars.append(char)
    return " ".join("".join(chars).split())


def _truncate(value: str, max_length: int) -> str:
    if max_length < 1:
        return ""
    if len(value) <= max_length:
        return value
    if max_length <= 3:
        return value[:max_length]
    return value[: max_length - 3].rstrip() + "..."


def sanitize_whisper_diagnostic_scalar(
    value: object | None,
    *,
    max_length: int = WHISPER_DIAGNOSTIC_MAX_LENGTH,
) -> str | None:
    """Return one clipboard-safe scalar without changing intentional paths.

    This boundary removes every newline/control character, collapses
    whitespace, and enforces a hard length limit. It intentionally does not
    redact paths because the dedicated Whisper cache-root field is diagnostic
    data the user explicitly copies.
    """
    normalized = _normalize_whitespace_and_controls(value)
    if not normalized:
        return None
    return _truncate(normalized, max_length)


def _redact_url_secrets(match: re.Match[str]) -> str:
    url = match.group(0)
    trailing = ""
    while url and url[-1] in ".,;:)]}":
        trailing = url[-1] + trailing
        url = url[:-1]

    scheme_end = url.find("://") + 3
    authority_end_candidates = [
        index
        for delimiter in ("/", "?", "#")
        if (index := url.find(delimiter, scheme_end)) >= 0
    ]
    authority_end = min(authority_end_candidates, default=len(url))
    authority = url[scheme_end:authority_end]
    if "@" in authority:
        authority = f"{_REDACTED}@{authority.rsplit('@', 1)[1]}"
        url = url[:scheme_end] + authority + url[authority_end:]

    secret_start_candidates = [
        index for delimiter in ("?", "#") if (index := url.find(delimiter)) >= 0
    ]
    if secret_start_candidates:
        url = url[: min(secret_start_candidates)] + f"?{_REDACTED}"
    return url + trailing


def _is_sensitive_credential_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    if "cookie" in compact:
        return True
    return compact.endswith(
        (
            "token",
            "password",
            "passwd",
            "pwd",
            "authorization",
            "auth",
            "apikey",
            "accesskey",
            "secret",
            "credential",
            "credentials",
            "sessionid",
            "sid",
        )
    )


def _redact_credential_value(match: re.Match[str]) -> str:
    if match.group("value") == _REDACTED:
        return match.group(0)
    if not _is_sensitive_credential_key(match.group("key")):
        return match.group(0)
    return f"{match.group('key')}={_REDACTED}"


def _redact_error_secrets_and_paths(value: str) -> str:
    value = _URL_RE.sub(_redact_url_secrets, value)
    value = _COOKIE_HEADER_RE.sub(
        lambda match: f"{match.group('header')}: {_REDACTED}",
        value,
    )
    value = _KEY_VALUE_RE.sub(_redact_credential_value, value)
    value = _QUOTED_ABSOLUTE_PATH_RE.sub(_REDACTED_PATH, value)
    value = _WINDOWS_ABSOLUTE_PATH_RE.sub(_REDACTED_PATH, value)
    value = _POSIX_PRIVATE_PATH_RE.sub(_REDACTED_PATH, value)
    return value


def sanitize_whisper_error_reason(
    value: object | None,
    *,
    fallback: str | None = None,
    max_length: int = WHISPER_DIAGNOSTIC_MAX_LENGTH,
) -> str | None:
    """Return one bounded, redacted, actionable Whisper failure reason.

    Only the first nonblank logical line is retained. Credential-like values,
    URL queries/fragments/user-info, and absolute Windows or POSIX user/temp
    paths are redacted before the hard length limit is applied.
    """
    first_line = ""
    for line in _stringify(value).splitlines() or [""]:
        first_line = _normalize_whitespace_and_controls(line)
        if first_line:
            break
    if not first_line:
        first_line = _normalize_whitespace_and_controls(fallback)
    if not first_line:
        return None
    if _STACK_BANNER_RE.search(first_line):
        generic = _normalize_whitespace_and_controls(fallback)
        if not generic or _STACK_BANNER_RE.search(generic):
            generic = _GENERIC_WHISPER_ERROR
        return _truncate(generic, max_length)
    redacted = _redact_error_secrets_and_paths(first_line)
    normalized = _normalize_whitespace_and_controls(redacted)
    if not normalized:
        normalized = _normalize_whitespace_and_controls(fallback)
    if not normalized:
        return None
    return _truncate(normalized, max_length)


def _display_optional(value: object | None) -> str:
    text = sanitize_whisper_diagnostic_scalar(value)
    return text or "n/a"


def _display_error_reason(value: object | None) -> str:
    text = sanitize_whisper_error_reason(value)
    return text or "n/a"


def _display_fallback(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "yes" if value else "no"


def _whisper_diagnostic_lines(whisper: WhisperDiagnosticsInput) -> list[str]:
    return [
        f"Whisper model: {_display_optional(whisper.model_name)}",
        f"Whisper phase: {_display_optional(whisper.phase)}",
        f"Whisper cache root: {_display_optional(whisper.cache_root)}",
        f"Whisper source kind: {_display_optional(whisper.source_kind)}",
        f"Whisper effective device: {_display_optional(whisper.effective_device)}",
        f"Whisper fallback used: {_display_fallback(whisper.fallback_used)}",
        f"Whisper error reason: {_display_error_reason(whisper.error_reason)}",
    ]


def build_diagnostics(
    *,
    url: str | None,
    action_kind: str | None,
    selected_title: str | None,
    selected_format: str | None,
    output_path: str | None,
    debug_log_path: str | None,
    transcript_source: str | None,
    transcript_languages: Iterable[str] | None,
    yt_dlp_version: str,
    ffmpeg_path: str | None,
    js_runtime: str | None,
    js_runtime_used: str | None,
    js_runtime_path: str | None,
    remote_components: Iterable[str] | None,
    cookies_mode: str | None,
    cookies_browser: str | None,
    last_error: str | None,
    last_error_reason: str | None,
    log_history: Iterable[str] | None,
    whisper: WhisperDiagnosticsInput | None = None,
) -> list[str]:
    action_kind_value = action_kind or "n/a"
    transcript_source_value = transcript_source or "n/a"
    transcript_languages_value = ", ".join(transcript_languages or []) or "n/a"
    js_runtime_value = js_runtime or "not found"
    cookies_mode_value = cookies_mode or "none"
    cookies_browser_value = cookies_browser or "n/a"
    js_runtime_used_value = js_runtime_used or "n/a"
    js_runtime_path_value = js_runtime_path or "n/a"
    remote_components_value = ", ".join(remote_components or []) or "none"
    log_tail = list(log_history or [])[-20:]
    last_error_value = last_error or "n/a"
    last_error_reason_value = last_error_reason or "n/a"
    if whisper is not None:
        # A selected Whisper snapshot is the authority for the current local
        # transcription failure. Never duplicate a raw exception through the
        # legacy fields or recent-log tail in the clipboard artifact.
        whisper_error = sanitize_whisper_error_reason(whisper.error_reason)
        last_error_value = whisper_error or "n/a"
        last_error_reason_value = whisper_error or "n/a"
        log_tail = [
            sanitized
            for entry in log_tail
            if (sanitized := sanitize_whisper_error_reason(entry)) is not None
        ]

    lines = [
        "Doggybag Diagnostics",
        f"URL: {url or 'n/a'}",
        f"Action kind: {action_kind_value}",
        f"Selected title: {selected_title or 'n/a'}",
        f"Selected format: {selected_format or 'n/a'}",
        f"Output path: {output_path or 'n/a'}",
        f"Debug log: {debug_log_path or 'n/a'}",
        f"Transcript source: {transcript_source_value}",
        f"Transcript languages: {transcript_languages_value}",
        f"yt-dlp version: {yt_dlp_version}",
        f"ffmpeg: {ffmpeg_path or 'not found'}",
        f"JS runtime: {js_runtime_value}",
        f"yt-dlp JS runtime: {js_runtime_used_value}",
        f"yt-dlp JS runtime path: {js_runtime_path_value}",
        f"EJS remote components: {remote_components_value}",
        f"Cookies mode: {cookies_mode_value}",
        f"Cookies browser: {cookies_browser_value}",
        f"Last error: {last_error_value}",
        f"Last classified error: {last_error_reason_value}",
    ]
    if whisper is not None:
        lines.extend(_whisper_diagnostic_lines(whisper))
    lines.append("-- Recent log --")
    lines.extend(log_tail)
    return lines
