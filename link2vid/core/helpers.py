"""Core helper utilities extracted from the UI layer."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import m3u8
import yt_dlp

_URL_IN_TEXT = re.compile(r"https?://\S+", re.IGNORECASE)
_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
_FFMPEG_PROGRESS_MS_RE = re.compile(r"^out_time_ms=(\d+(?:\.\d+)?)$")

ProgressHook = Callable[[float, float | None, float | None], None]


def normalize_url(raw_url: str | None) -> str:
    """Normalize known URL variants and enforce https."""
    u = (raw_url or "").strip()
    if not u:
        return u
    if u.lower().startswith("http://"):
        u = "https://" + u[7:]
    return u


def _trim_trailing_url_junk(u: str) -> str:
    return u.rstrip(").,;\"'\\]}")


def is_usable_http_url(url: str | None) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    parsed = urlparse(u)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def url_from_clipboard_text(text: str | None) -> str | None:
    """Pick a normalized http(s) URL from plain text (e.g. system clipboard), or None."""
    raw = (text or "").strip()
    if not raw:
        return None
    first_line = raw.splitlines()[0].strip()
    candidates: list[str] = [first_line]
    candidates.extend(m.group(0) for m in _URL_IN_TEXT.finditer(first_line))
    seen: set[str] = set()
    for c in candidates:
        c = _trim_trailing_url_junk(c.strip())
        if not c or c in seen:
            continue
        seen.add(c)
        norm = normalize_url(c)
        if is_usable_http_url(norm):
            return norm
    return None


def sanitize_filename(name: str, *, max_length: int = 120) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return "video"
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "video"


def unique_output_path(folder: str, base_name: str, ext: str = "mp4") -> str:
    safe = sanitize_filename(base_name)
    candidate = os.path.join(folder, f"{safe}.{ext}")
    if not os.path.exists(candidate):
        return candidate
    for number in range(2, 100):
        numbered = os.path.join(folder, f"{safe} ({number}).{ext}")
        if not os.path.exists(numbered):
            return numbered
    return os.path.join(folder, f"{safe} ({os.getpid()}).{ext}")


def get_format_options() -> list[dict[str, str]]:
    return [
        {"label": "Best (A+V)", "format": "bestvideo+bestaudio/best"},
        {"label": "Best video", "format": "bestvideo"},
        {"label": "Best audio", "format": "bestaudio"},
    ]


def get_yt_dlp_version() -> str:
    version = getattr(yt_dlp, "__version__", None)
    if version:
        return version
    try:
        from yt_dlp import version as ytdlp_version

        return getattr(ytdlp_version, "__version__", getattr(ytdlp_version, "VERSION", "unknown"))
    except Exception:
        return "unknown"


def parse_ffmpeg_time_seconds(line: str) -> float | None:
    match = _FFMPEG_TIME_RE.search(line)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_ffmpeg_progress_ms(line: str) -> float | None:
    match = _FFMPEG_PROGRESS_MS_RE.match(line.strip())
    if not match:
        return None
    return float(match.group(1)) / 1000.0


def _ffmpeg_header_args(headers: dict | None) -> tuple[str, str | None]:
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    header_block = None
    if headers:
        ua = headers.get("User-Agent", ua)
        header_block = "\r\n".join(f"{k}: {v}" for k, v in headers.items()) + "\r\n"
    return ua, header_block


def probe_media_duration(media_url: str, headers: dict | None = None) -> float | None:
    if media_url.lower().endswith(".m3u8") or "m3u8" in media_url.lower():
        try:
            playlist = m3u8.load(media_url, headers=headers or {})
            if playlist.is_variant and playlist.playlists:
                best = max(
                    playlist.playlists,
                    key=lambda variant: variant.stream_info.bandwidth or 0,
                )
                media_url = urljoin(media_url, best.uri)
                playlist = m3u8.load(media_url, headers=headers or {})
            duration = sum(segment.duration or 0 for segment in playlist.segments)
            if duration > 0:
                return duration
        except Exception:
            pass

    ua, header_block = _ffmpeg_header_args(headers)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
    ]
    if header_block:
        cmd.extend(["-headers", header_block])
    cmd.extend(["-user_agent", ua, media_url])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return None
        value = (proc.stdout or "").strip()
        duration = float(value)
        return duration if duration > 0 else None
    except Exception:
        return None


def _emit_progress(
    progress_hook: ProgressHook | None,
    *,
    elapsed_seconds: float,
    duration_seconds: float | None,
    last_fraction: float,
) -> float:
    if progress_hook is None:
        return last_fraction
    if duration_seconds and duration_seconds > 0:
        fraction = min(elapsed_seconds / duration_seconds, 0.99)
    else:
        fraction = min(0.05 + (elapsed_seconds / max(elapsed_seconds + 120, 600)) * 0.9, 0.95)
    if fraction - last_fraction >= 0.005 or fraction >= 0.99:
        progress_hook(fraction, elapsed_seconds, duration_seconds)
        return fraction
    return last_fraction


def download_with_ffmpeg(
    m3u8_url: str,
    output_file: str,
    headers: dict | None = None,
    *,
    progress_hook: ProgressHook | None = None,
    duration_seconds: float | None = None,
) -> None:
    ua, header_block = _ffmpeg_header_args(headers)
    if duration_seconds is None:
        duration_seconds = probe_media_duration(m3u8_url, headers)

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-nostats",
        "-headers",
        header_block if header_block else "",
        "-user_agent",
        ua,
        "-i",
        m3u8_url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        output_file,
    ]
    cmd = [c for c in cmd if c]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_lines: list[str] = []
    last_fraction = 0.0
    for line in proc.stdout:
        output_lines.append(line)
        elapsed_seconds = parse_ffmpeg_progress_ms(line)
        if elapsed_seconds is None:
            elapsed_seconds = parse_ffmpeg_time_seconds(line)
        if elapsed_seconds is not None:
            last_fraction = _emit_progress(
                progress_hook,
                elapsed_seconds=elapsed_seconds,
                duration_seconds=duration_seconds,
                last_fraction=last_fraction,
            )
    proc.wait()
    if proc.returncode != 0:
        tail = "".join(output_lines[-20:]).strip()
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {tail}")
    if not os.path.isfile(output_file) or os.path.getsize(output_file) == 0:
        raise RuntimeError(f"ffmpeg produced no output at {output_file}")
    if progress_hook is not None:
        progress_hook(1.0, duration_seconds, duration_seconds)
