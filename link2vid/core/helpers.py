"""Core helper utilities extracted from the UI layer."""

from __future__ import annotations

import re
import subprocess
import yt_dlp


def normalize_url(raw_url: str | None) -> str:
    """Normalize known URL variants and enforce https."""
    u = (raw_url or "").strip()
    if not u:
        return u
    if u.lower().startswith("http://"):
        u = "https://" + u[7:]
    u = re.sub(r"^(https?://)x\.com/", r"\1twitter.com/", u, flags=re.IGNORECASE)
    return u


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


def download_with_ffmpeg(m3u8_url: str, output_file: str, headers: dict | None = None) -> None:
    header_block = None
    ua = "Mozilla/5.0"
    if headers:
        ua = headers.get("User-Agent", ua)
        header_block = "\\r\\n".join(f"{k}: {v}" for k, v in headers.items()) + "\\r\\n"
    cmd = [
        "ffmpeg",
        "-y",
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
    for _line in proc.stdout:
        pass
    proc.wait()
