"""Diagnostics assembly helpers."""

from __future__ import annotations

from typing import Iterable


def build_diagnostics(
    *,
    url: str | None,
    selected_title: str | None,
    selected_format: str | None,
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
) -> list[str]:
    js_runtime_value = js_runtime or "not found"
    cookies_mode_value = cookies_mode or "none"
    cookies_browser_value = cookies_browser or "n/a"
    js_runtime_used_value = js_runtime_used or "n/a"
    js_runtime_path_value = js_runtime_path or "n/a"
    remote_components_value = ", ".join(remote_components or []) or "none"
    log_tail = list(log_history or [])[-20:]

    return [
        "Link2Vid Diagnostics",
        f"URL: {url or 'n/a'}",
        f"Selected title: {selected_title or 'n/a'}",
        f"Selected format: {selected_format or 'n/a'}",
        f"yt-dlp version: {yt_dlp_version}",
        f"ffmpeg: {ffmpeg_path or 'not found'}",
        f"JS runtime: {js_runtime_value}",
        f"yt-dlp JS runtime: {js_runtime_used_value}",
        f"yt-dlp JS runtime path: {js_runtime_path_value}",
        f"EJS remote components: {remote_components_value}",
        f"Cookies mode: {cookies_mode_value}",
        f"Cookies browser: {cookies_browser_value}",
        f"Last error: {last_error or 'n/a'}",
        f"Last classified error: {last_error_reason or 'n/a'}",
        "-- Recent log --",
        *log_tail,
    ]
