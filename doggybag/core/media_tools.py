"""Local media helpers: section time ranges, ffmpeg cuts and ffprobe facts."""

from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .helpers import _subprocess_no_window_kwargs

SECTION_MODES = ("exact", "copy")


def parse_timestamp(text: str) -> float:
    """Parse ``SS``, ``MM:SS`` or ``HH:MM:SS`` (seconds may have a fraction)."""
    parts = (text or "").strip().split(":")
    if not parts or len(parts) > 3 or any(not part.strip() for part in parts):
        raise ValueError(f"invalid time {text!r}; use SS, MM:SS or HH:MM:SS")
    try:
        seconds = float(parts[-1])
        whole = [int(part) for part in parts[:-1]]
    except ValueError:
        raise ValueError(f"invalid time {text!r}; use SS, MM:SS or HH:MM:SS") from None
    if seconds < 0 or any(value < 0 for value in whole) or not math.isfinite(seconds):
        raise ValueError(f"invalid time {text!r}")
    if len(parts) > 1 and seconds >= 60:
        raise ValueError(f"invalid time {text!r}: seconds must be under 60")
    if len(parts) == 3 and whole[1] >= 60:
        raise ValueError(f"invalid time {text!r}: minutes must be under 60")
    total = seconds
    for multiplier, value in zip((60, 3600)[: len(whole)], reversed(whole)):
        total += value * multiplier
    return total


def parse_time_range(spec: str) -> tuple[float, float | None]:
    """Parse ``START-END``; either side may be empty (``90-`` or ``-1:00``)."""
    if "-" not in (spec or ""):
        raise ValueError(f"invalid section {spec!r}; use START-END, e.g. 1:23-2:45")
    start_text, end_text = spec.split("-", 1)
    start = parse_timestamp(start_text) if start_text.strip() else 0.0
    end = parse_timestamp(end_text) if end_text.strip() else None
    if end is not None and end <= start:
        raise ValueError(f"invalid section {spec!r}: end must be after start")
    return start, end


def format_stamp(seconds: float) -> str:
    """``83.5`` -> ``00.01.23.500``; filename-safe (no colons)."""
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    text = f"{hours:02d}.{minutes:02d}.{secs:02d}"
    return f"{text}.{millis:03d}" if millis else text


def section_stem(stem: str, start: float, end: float | None) -> str:
    end_text = format_stamp(end) if end is not None else "end"
    return f"{stem}.clip-{format_stamp(start)}-{end_text}"


def section_extension(source: str | Path, *, mode: str, audio_only: bool) -> str:
    if mode == "copy":
        return Path(source).suffix.lstrip(".") or "mp4"
    return "mp3" if audio_only else "mp4"


def cut_section(
    source: str | Path,
    output: str | Path,
    start: float,
    end: float | None,
    *,
    mode: str = "exact",
    audio_only: bool = False,
    run=subprocess.run,
) -> None:
    """Cut ``source[start:end]`` into ``output`` with ffmpeg; never overwrites.

    ``exact`` re-encodes for frame-accurate edges; ``copy`` is lossless and fast
    but snaps the start to the previous keyframe.
    """
    if mode not in SECTION_MODES:
        raise ValueError(f"unknown section mode {mode!r}")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-n", "-ss", f"{start:.3f}", "-i", str(source)]
    if end is not None:
        cmd += ["-t", f"{end - start:.3f}"]
    if mode == "copy":
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    elif audio_only:
        cmd += ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
        ]
    cmd.append(str(output))
    # subprocess.run kills the child if we are interrupted while it runs.
    proc = run(cmd, capture_output=True, text=True, check=False, **_subprocess_no_window_kwargs())
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-20:])
        raise RuntimeError(f"ffmpeg cut failed (exit {proc.returncode}): {tail}")
    if not os.path.isfile(output) or os.path.getsize(output) == 0:
        raise RuntimeError(f"ffmpeg produced no output at {output}")


@dataclass
class MediaFacts:
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _frame_rate(text: str | None) -> float | None:
    if not text or text in ("0/0", "0"):
        return None
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            value = float(num) / float(den)
        else:
            value = float(text)
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 3) if math.isfinite(value) and value > 0 else None


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_ffprobe_json(text: str) -> MediaFacts:
    data = json.loads(text or "{}")
    facts = MediaFacts(duration=_finite((data.get("format") or {}).get("duration")))
    for stream in data.get("streams") or []:
        kind = stream.get("codec_type")
        if kind == "video" and facts.vcodec is None:
            disposition = stream.get("disposition") or {}
            if disposition.get("attached_pic"):
                continue
            facts.vcodec = stream.get("codec_name")
            facts.width = stream.get("width")
            facts.height = stream.get("height")
            facts.fps = _frame_rate(stream.get("avg_frame_rate")) or _frame_rate(stream.get("r_frame_rate"))
        elif kind == "audio" and facts.acodec is None:
            facts.acodec = stream.get("codec_name")
    return facts


def probe_media_file(path: str | Path, *, run=subprocess.run) -> MediaFacts | None:
    """Duration, resolution, frame rate and codecs via ffprobe; None if unavailable."""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = run(cmd, capture_output=True, text=True, check=False, **_subprocess_no_window_kwargs())
    except (OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return parse_ffprobe_json(proc.stdout)
    except (ValueError, TypeError, AttributeError):
        return None


__all__ = [
    "MediaFacts",
    "SECTION_MODES",
    "cut_section",
    "format_stamp",
    "parse_ffprobe_json",
    "parse_time_range",
    "parse_timestamp",
    "probe_media_file",
    "section_extension",
    "section_stem",
]
