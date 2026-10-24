"""Output contract for the headless CLI: envelope, errors, exit codes, reporter."""

from __future__ import annotations

import json
import math
import time
from typing import TextIO
from urllib.parse import urlparse

from ..app.workflow import entry_url, is_direct_entry
from ..core.error_classification import classify_error, get_error_guidance
from ..core.errors import CookiesRequiredError, DownloadStalledError, NoTranscriptAvailableError

SCHEMA_VERSION = "doggybag.cli/1"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3
EXIT_NEEDS_ACTION = 4
EXIT_NOTHING = 5
EXIT_DEPENDENCY = 6
EXIT_INTERRUPTED = 130

EXIT_CODES = {
    EXIT_OK: "ok: every requested item succeeded or already existed",
    EXIT_FAILED: "failed: see error.code",
    EXIT_USAGE: "usage: bad flags, section, items, file or model name",
    EXIT_PARTIAL: "partial: some items succeeded, some failed or were skipped",
    EXIT_NEEDS_ACTION: "needs action: re-run with the flags in error.next_steps",
    EXIT_NOTHING: "nothing to get: no media, transcript or speech found",
    EXIT_DEPENDENCY: "dependency missing: ffmpeg or Faster Whisper",
    EXIT_INTERRUPTED: "interrupted (Ctrl+C)",
}

# code -> (exit code, meaning)
ERROR_CODES = {
    "cookies_required": (EXIT_NEEDS_ACTION, "The site wants a signed-in session."),
    "credentials_required": (EXIT_NEEDS_ACTION, "The site needs a username and password."),
    "browser_automation_required": (EXIT_NEEDS_ACTION, "Only a browser scrape (visible Chrome) can find media."),
    "items_required": (EXIT_NEEDS_ACTION, "The URL has several entries; choose with --items."),
    "format_unavailable": (EXIT_NEEDS_ACTION, "The requested format is not offered."),
    "login_failed": (EXIT_FAILED, "Sign-in was rejected."),
    "no_media_found": (EXIT_NOTHING, "No downloadable media was found."),
    "no_transcript": (EXIT_NOTHING, "The site offers no matching subtitles or captions."),
    "no_speech": (EXIT_NOTHING, "Whisper found no speech."),
    "ffmpeg_missing": (EXIT_DEPENDENCY, "ffmpeg is not on PATH."),
    "whisper_unavailable": (EXIT_DEPENDENCY, "Faster Whisper is not installed or failed to load."),
    "whisper_model_invalid": (EXIT_USAGE, "Unknown Whisper model name."),
    "audio_acquisition_failed": (EXIT_FAILED, "Could not get audio for transcription."),
    "download_stalled": (EXIT_FAILED, "YouTube throttled every player client."),
    "media_forbidden": (EXIT_FAILED, "The media stream answered 403 on every player client."),
    "js_runtime": (EXIT_FAILED, "yt-dlp's JavaScript challenge solver failed."),
    "network": (EXIT_FAILED, "Network error or rate limit."),
    "extractor": (EXIT_FAILED, "yt-dlp could not extract this page; try updating yt-dlp."),
    "ffmpeg_failed": (EXIT_FAILED, "ffmpeg failed while merging, converting or cutting."),
    "verification_failed": (EXIT_FAILED, "The download reported success but no file was found."),
    "download_failed": (EXIT_FAILED, "The download failed."),
    "transcription_failed": (EXIT_FAILED, "Whisper transcription failed."),
    "invalid_input": (EXIT_USAGE, "Invalid arguments."),
    "interrupted": (EXIT_INTERRUPTED, "Interrupted."),
}

_REASON_CODES = {
    "format unavailable": "format_unavailable",
    "no transcript": "no_transcript",
    "js runtime": "js_runtime",
    "network/rate-limit": "network",
    "media 403/player client": "media_forbidden",
    "media stall/player client": "download_stalled",
    "extractor drift/SABR": "extractor",
    "ffmpeg": "ffmpeg_failed",
}

# Exit priority when every item failed for different reasons.
_EXIT_PRIORITY = (EXIT_NEEDS_ACTION, EXIT_USAGE, EXIT_DEPENDENCY, EXIT_NOTHING, EXIT_FAILED)


class CliError(Exception):
    """A failure with a stable code, raised inside the CLI."""

    def __init__(self, code: str, message: str, *, next_steps: list[dict] | None = None, **extra) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_steps = next_steps
        self.extra = extra


def exit_code_for(code: str | None) -> int:
    if not code:
        return EXIT_OK
    return ERROR_CODES.get(code, (EXIT_FAILED, ""))[0]


def _chain(exc: BaseException | None):
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.extend([getattr(current, "original", None), current.__cause__, current.__context__])


def error_code_for(exc: BaseException) -> str:
    """Exception types first; ``classify_error`` text matching only as a fallback."""
    from ..core.audio_source import AudioAcquisitionError
    from ..core.gdcvault import GdcVaultLoginError, GdcVaultLoginRequired
    from ..core.whisper_transcription import InvalidWhisperModelError, NoSpeechError

    for err in _chain(exc):
        if isinstance(err, CliError):
            return err.code
        if isinstance(err, KeyboardInterrupt):
            return "interrupted"
        if isinstance(err, CookiesRequiredError):
            return "cookies_required"
        if isinstance(err, GdcVaultLoginRequired):
            return "credentials_required"
        if isinstance(err, GdcVaultLoginError):
            return "login_failed"
        if isinstance(err, NoTranscriptAvailableError):
            return "no_transcript"
        if isinstance(err, NoSpeechError):
            return "no_speech"
        if isinstance(err, InvalidWhisperModelError):
            return "whisper_model_invalid"
        if isinstance(err, AudioAcquisitionError):
            return "audio_acquisition_failed"
        if isinstance(err, DownloadStalledError):
            return "download_stalled"
    reason, _hint = classify_error(_error_text(exc))
    return _REASON_CODES.get(reason, "download_failed")


def _error_text(exc: BaseException) -> str:
    original = getattr(exc, "original", None)
    return str(original or exc) or type(exc).__name__


def next_steps_for(code: str, *, url: str | None = None, **ctx) -> list[dict]:
    if code == "cookies_required":
        return [
            {
                "add_flags": ["--cookies-browser", "firefox"],
                "note": "Use the browser you are signed in with (brave, chrome, edge, firefox). "
                "Close it fully first; Chrome/Edge/Brave cookies are often unreadable while running.",
            },
            {
                "add_flags": ["--cookies-file", "PATH"],
                "note": "A Netscape cookies.txt exported from a signed-in browser.",
            },
        ]
    if code == "credentials_required":
        return [
            {
                "set_env": ["DOGGYBAG_USERNAME", "DOGGYBAG_PASSWORD"],
                "note": "Prefer environment variables; --username/--password also work.",
            }
        ]
    if code == "browser_automation_required":
        return [
            {
                "add_flags": ["--allow-browser"],
                "note": "Opens a visible Chrome window and signs in with DOGGYBAG_USERNAME/"
                "DOGGYBAG_PASSWORD. Ask the user before doing this.",
            }
        ]
    if code == "items_required":
        return [
            {"add_flags": ["--items", "1"], "note": "Pick entries by index: 3, 1,4,6-9, or all."},
            {"add_flags": ["--items", "all"], "note": f"All {ctx.get('count', 'listed')} entries."},
        ]
    if code == "format_unavailable":
        return [
            {"add_flags": ["--format-fallback", "best"], "note": "Accept the best single file instead."},
            {"command": "info --formats", "note": "List the format ids the site offers."},
        ]
    if code == "ffmpeg_missing":
        return [{"note": "Install ffmpeg and put it on PATH, then run `doctor`."}]
    if code == "whisper_unavailable":
        return [{"note": "pip install -r requirements.txt (faster-whisper, ctranslate2), then run `doctor`."}]
    if code == "whisper_model_invalid":
        return [{"command": "doctor", "note": "Lists the available Whisper models."}]
    if code == "no_transcript":
        return [
            {"command": "transcript --list", "note": "See which languages the site offers."},
            {"command": "transcribe", "note": "Transcribe locally with Whisper instead."},
        ]
    if code in ("js_runtime", "extractor", "media_forbidden", "download_stalled"):
        return [{"note": "Update yt-dlp and yt-dlp-ejs (pip install -U yt-dlp yt-dlp-ejs) and make sure "
                 "deno or node is on PATH, then retry."}]
    if code == "network":
        return [{"note": "Wait and retry; the site may be rate limiting."}]
    return []


def error_info(exc: BaseException, *, url: str | None = None, stage: str | None = None, **ctx) -> dict:
    code = error_code_for(exc)
    text = _error_text(exc)
    if isinstance(exc, CliError):
        message = exc.message
        steps = exc.next_steps if exc.next_steps is not None else next_steps_for(code, url=url, **ctx)
        extra = dict(exc.extra)
    else:
        message = _one_line(text)
        steps = next_steps_for(code, url=url, **ctx)
        extra = {}
    reason, hint = classify_error(text)
    info = {
        "code": code,
        "message": message,
        "type": type(exc).__name__,
        "reason": None if reason == "unknown" else reason,
        "hint": hint.strip().removeprefix("(Hint:").removesuffix(")").strip() or None,
        "url": url,
        "stage": stage,
        "next_steps": steps,
    }
    guidance = get_error_guidance(text)
    if guidance:
        info["guidance"] = {"title": guidance[1], "message": guidance[2]}
    info.update(extra)
    return info


def make_error(code: str, message: str, *, url: str | None = None, **ctx) -> dict:
    return error_info(CliError(code, message), url=url, **ctx)


def _one_line(text: str, limit: int = 500) -> str:
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


# ──────────────────────────────────────────────────────────
# Entry projection (never leaks headers, signed URLs or cookies)
# ──────────────────────────────────────────────────────────
def _finite(value) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _text(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _http_url(value) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def _site(entry: dict) -> str | None:
    site = entry.get("extractor_key") or entry.get("extractor") or entry.get("ie_key")
    if site and str(site).lower() not in ("generic", "html5mediaembed"):
        return str(site)
    host = urlparse(str(entry.get("webpage_url") or entry.get("url") or "")).netloc.lower()
    return host.removeprefix("www.") or None


def duration_text(duration) -> str | None:
    total = _finite(duration)
    if total is None:
        return None
    minutes, seconds = divmod(int(total), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def format_summary(fmt: dict) -> dict:
    return {
        "format_id": _text(fmt.get("format_id")),
        "ext": _text(fmt.get("ext")),
        "resolution": _text(fmt.get("resolution")),
        "width": _finite(fmt.get("width")),
        "height": _finite(fmt.get("height")),
        "fps": _finite(fmt.get("fps")),
        "vcodec": _text(fmt.get("vcodec")),
        "acodec": _text(fmt.get("acodec")),
        "filesize": _finite(fmt.get("filesize")),
        "filesize_approx": _finite(fmt.get("filesize_approx")),
        "tbr": _finite(fmt.get("tbr")),
        "protocol": _text(fmt.get("protocol")),
        "note": _text(fmt.get("format_note")),
    }


def entry_summary(
    entry: dict,
    *,
    index: int,
    fallback_url: str | None,
    tracks=None,
    include_formats: bool = False,
) -> dict:
    direct = is_direct_entry(entry)
    summary = {
        "index": index,
        "id": _text(entry.get("id")) if not isinstance(entry.get("id"), int) else str(entry["id"]),
        "title": _text(entry.get("title")) or "Untitled",
        # Direct media URLs can carry signed tokens; only page URLs are reported.
        "url": None if direct else entry_url(entry, fallback_url=fallback_url),
        "webpage_url": None if direct else _http_url(entry.get("webpage_url") or entry.get("original_url")),
        "site": _site(entry) if not direct else "direct media",
        "route": "direct" if direct else "yt-dlp",
        "uploader": _text(entry.get("uploader") or entry.get("channel") or entry.get("uploader_id")),
        "duration": _finite(entry.get("duration")),
        "duration_text": duration_text(entry.get("duration")),
        "upload_date": _text(entry.get("upload_date")),
        "live_status": _text(entry.get("live_status")),
        "availability": _text(entry.get("availability")),
        "thumbnail": _http_url(entry.get("thumbnail")),
    }
    playlist = _text(entry.get("playlist_title") or entry.get("playlist"))
    if playlist:
        summary["playlist"] = {"title": playlist, "index": _finite(entry.get("playlist_index"))}
    chapters = []
    for chapter in entry.get("chapters") or []:
        if isinstance(chapter, dict):
            chapters.append(
                {
                    "title": _text(chapter.get("title")),
                    "start": _finite(chapter.get("start_time")),
                    "end": _finite(chapter.get("end_time")),
                }
            )
    summary["chapters"] = chapters
    summary["transcript_tracks"] = (
        None
        if tracks is None
        else [{"source": t.source, "language": t.language, "label": t.label} for t in tracks]
    )
    if include_formats:
        summary["formats"] = [
            format_summary(fmt) for fmt in (entry.get("formats") or []) if isinstance(fmt, dict)
        ]
    return summary


def json_safe(value):
    """Recursively replace values JSON cannot carry (NaN, objects) with plain data."""
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


# ──────────────────────────────────────────────────────────
# Reporter
# ──────────────────────────────────────────────────────────
class Reporter:
    """Owns stdout (results) and stderr (logs, progress)."""

    def __init__(
        self,
        *,
        stdout: TextIO,
        stderr: TextIO | None,
        mode: str = "json",
        quiet: bool = False,
        log_file: str | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.mode = mode
        self.quiet = quiet
        self.log_file = log_file
        self._last_progress: dict = {}

    def log(self, message: str) -> None:
        if not message:
            return
        if not self.quiet and self.stderr is not None:
            try:
                self.stderr.write(f"{message}\n")
                self.stderr.flush()
            except (OSError, ValueError):
                pass
        if self.log_file:
            try:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                with open(self.log_file, "a", encoding="utf-8") as handle:
                    handle.write(f"{stamp} {message}\n")
            except OSError:
                pass

    def event(self, name: str, **payload) -> None:
        if self.mode != "stream":
            return
        self._write_stdout(json.dumps(json_safe({"event": name, **payload}), ensure_ascii=False) + "\n")

    def progress(self, index, phase: str, fraction: float | None, **extra) -> None:
        """Throttled to 1% steps or 0.5 s per (item, phase)."""
        now = time.monotonic()
        key = (index, phase)
        last_fraction, last_time = self._last_progress.get(key, (None, 0.0))
        done = fraction is not None and fraction >= 1.0
        if (
            not done
            and last_fraction is not None
            and fraction is not None
            and fraction - last_fraction < 0.01
            and now - last_time < 0.5
        ):
            return
        if not done and last_fraction is not None and now - last_time < 0.5 and fraction is None:
            return
        self._last_progress[key] = (fraction, now)
        self.event("progress", index=index, phase=phase, fraction=fraction, **extra)
        if self.mode != "stream" and not self.quiet and fraction is not None:
            pct = int(fraction * 100)
            last_pct = int((last_fraction or 0) * 100)
            if done or last_fraction is None or pct // 10 != last_pct // 10:
                self.log(f"[{index}] {phase} {pct}%")

    def result(self, envelope: dict) -> None:
        data = json_safe(envelope)
        if self.mode == "stream":
            self._write_stdout(json.dumps({"event": "result", **data}, ensure_ascii=False) + "\n")
        elif self.mode == "text":
            self._write_stdout(render_text(data))
        else:
            self._write_stdout(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def _write_stdout(self, text: str) -> None:
        self.stdout.write(text)
        self.stdout.flush()


def render_text(envelope: dict) -> str:
    lines = [f"{envelope.get('command')}: {envelope.get('status')} (exit {envelope.get('exit_code')})"]
    for item in envelope.get("items") or []:
        status = item.get("status")
        tag = {"ok": "OK  ", "already_exists": "HAVE", "skipped": "SKIP"}.get(status, "FAIL")
        index = item.get("index")
        prefix = f"[{index}] " if index is not None else ""
        lines.append(f"{tag} {prefix}{item.get('title') or item.get('source_url') or ''}".rstrip())
        for file in item.get("files") or []:
            facts = ""
            if file.get("height"):
                facts = f" ({file.get('width')}x{file.get('height')}"
                facts += f" {file['fps']}fps)" if file.get("fps") else ")"
            lines.append(f"     {file.get('kind')}: {file.get('path')}{facts}")
        if item.get("error"):
            lines.append(f"     error [{item['error'].get('code')}]: {item['error'].get('message')}")
        for warning in item.get("warnings") or []:
            lines.append(f"     warning [{warning.get('code')}]: {warning.get('message')}")
    for key, value in (envelope.get("report") or {}).items():
        lines.append(f"{key}: {value}")
    for warning in envelope.get("warnings") or []:
        lines.append(f"warning [{warning.get('code')}]: {warning.get('message')}")
    error = envelope.get("error")
    steps = list((error or {}).get("next_steps") or [])
    if error:
        lines.append(f"error [{error.get('code')}]: {error.get('message')}")
    for item in envelope.get("items") or []:
        steps.extend((item.get("error") or {}).get("next_steps") or [])
    seen = set()
    for step in steps:
        text = " ".join(step.get("add_flags") or []) or step.get("command") or ", ".join(step.get("set_env") or [])
        line = f"Try: {text} - {step.get('note')}" if text else f"Try: {step.get('note')}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines) + "\n"


__all__ = [
    "CliError",
    "ERROR_CODES",
    "EXIT_CODES",
    "Reporter",
    "SCHEMA_VERSION",
    "entry_summary",
    "error_code_for",
    "error_info",
    "exit_code_for",
    "format_summary",
    "json_safe",
    "make_error",
    "next_steps_for",
    "render_text",
]
