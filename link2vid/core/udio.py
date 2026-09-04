"""Public Udio song extraction.

yt-dlp refuses udio.com, and Udio sign-in is an email magic link, so the
Selenium username/password path cannot work. Public song pages already expose
a downloadable MP3 through Udio's unauthenticated songs API.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlparse
import re

import requests

from .extractors import LogFn

UDIO_HOSTS = frozenset({"udio.com", "www.udio.com"})
UDIO_SONG_ID_RE = re.compile(r"/songs/([A-Za-z0-9]+)")
UDIO_SONGS_API = "https://www.udio.com/api/songs"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0)"

FetchSongsFn = Callable[[list[str]], list[dict]]


def is_udio_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return host in UDIO_HOSTS


def udio_song_id_from_url(url: str) -> str | None:
    path = urlparse(url).path or ""
    match = UDIO_SONG_ID_RE.search(path)
    return match.group(1) if match else None


def _default_fetch_songs(song_ids: list[str]) -> list[dict]:
    params = {"songIds": ",".join(song_ids)}
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    response = requests.get(UDIO_SONGS_API, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    payload = response.json()
    songs = payload.get("songs") if isinstance(payload, dict) else None
    return songs if isinstance(songs, list) else []


def _entry_from_song(song: dict, *, page_url: str) -> dict | None:
    media_url = song.get("song_path")
    if not isinstance(media_url, str) or not media_url.strip():
        return None
    title = str(song.get("title") or "").strip() or "Udio track"
    artist = str(song.get("artist") or "").strip()
    display = f"{artist} - {title}" if artist else title
    entry = {
        "id": song.get("id") or udio_song_id_from_url(page_url),
        "title": display,
        "webpage_url": media_url.strip(),
        "original_url": page_url,
        "formats": [
            {
                "format_id": "best",
                "ext": "mp3",
                "filesize_approx": "N/A",
            }
        ],
    }
    thumbnail = song.get("image_path")
    if isinstance(thumbnail, str) and thumbnail.strip():
        entry["thumbnail"] = thumbnail.strip()
    duration = song.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        entry["duration"] = float(duration)
    return entry


def extract_udio_entries(
    url: str,
    *,
    log: LogFn | None = None,
    fetch_songs: FetchSongsFn | None = None,
) -> list[dict]:
    """Return playable entries for a public Udio song URL, or []."""
    logger = log or (lambda _msg: None)
    if not is_udio_url(url):
        return []

    song_id = udio_song_id_from_url(url)
    if not song_id:
        logger("[Udio] Not a public /songs/ URL; email-link login cannot be automated.")
        return []

    logger(f"[Udio] Fetching public song metadata for {song_id} …")
    loader = fetch_songs or _default_fetch_songs
    songs = loader([song_id])
    entries: list[dict] = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        entry = _entry_from_song(song, page_url=url)
        if entry:
            entries.append(entry)
    if entries:
        logger(f"[Udio] Found {len(entries)} public track(s).")
    else:
        logger("[Udio] No public audio URL on this song page.")
    return entries
