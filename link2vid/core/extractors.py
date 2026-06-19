"""Non-UI extractors for special-case sites."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import re
import urllib.parse

import m3u8
import requests

LogFn = Callable[[str], None]

M3U8_URL_RE = re.compile(r"https?://[^\"'\s<>]+\.m3u8[^\"'\s<>]*", re.I)
MP4_URL_RE = re.compile(r"https?://[^\"'\s<>]+\.mp4[^\"'\s<>]*", re.I)
MUX_URL_RE = re.compile(r"https?://stream\.mux\.com/[^\"'\s<>]+", re.I)


def _dedupe_preserve_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


M3U8_URL_RE = re.compile(r"https?://[^\"'\s<>]+\.m3u8[^\"'\s<>]*", re.I)
MP4_URL_RE = re.compile(r"https?://[^\"'\s<>]+\.mp4[^\"'\s<>]*", re.I)
MUX_URL_RE = re.compile(r"https?://stream\.mux\.com/[^\"'\s<>]+", re.I)
TITLE_TAG_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I)
OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content="([^"]*)"',
    re.I,
)
OG_TITLE_ALT_RE = re.compile(
    r'<meta[^>]+content="([^"]*)"[^>]+property=["\']og:title["\']',
    re.I,
)
SITE_TITLE_SUFFIXES = (
    " | AI Advantage Club",
    " - AI Advantage Club",
    " | Circle",
    " - Circle",
)


def _clean_page_title(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", (title or "").strip())
    for suffix in SITE_TITLE_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    if " | " in cleaned:
        cleaned = cleaned.split(" | ", 1)[0].strip()
    return cleaned


def extract_page_title(html: str) -> str | None:
    for pattern in (OG_TITLE_RE, OG_TITLE_ALT_RE):
        match = pattern.search(html)
        if match:
            cleaned = _clean_page_title(match.group(1))
            if cleaned:
                return cleaned
    match = TITLE_TAG_RE.search(html)
    if match:
        cleaned = _clean_page_title(match.group(1))
        if cleaned:
            return cleaned
    return None


def title_from_page_url(page_url: str) -> str:
    parts = [segment for segment in urllib.parse.urlparse(page_url).path.split("/") if segment]
    slug = parts[-1] if parts else ""
    if slug.lower() in {"c", "posts", "lessons", "courses", "p"} and len(parts) >= 2:
        slug = parts[-2]
    label = re.sub(r"[-_]+", " ", slug).strip()
    return label.title() if label else "Video"


def _mux_playback_id(media_url: str) -> str | None:
    match = re.search(r"stream\.mux\.com/([A-Za-z0-9]+)", media_url, re.I)
    return match.group(1) if match else None


def _title_near_playback_id(html: str, playback_id: str) -> str | None:
    idx = html.find(playback_id)
    if idx < 0:
        return None
    window = html[max(0, idx - 500): idx + 500]
    for pattern in (
        r'"title"\s*:\s*"([^"]{3,120})"',
        r'"name"\s*:\s*"([^"]{3,120})"',
        r'aria-label="([^"]{3,120})"',
        r">([^<]{3,120})</h[1-6]>",
    ):
        match = re.search(pattern, window, re.I)
        if match:
            candidate = re.sub(r"\s+", " ", match.group(1)).strip()
            if candidate and not candidate.lower().startswith("http"):
                return candidate
    return None


def guess_video_titles(html: str, media_urls: list[str]) -> list[str | None]:
    titles: list[str | None] = []
    for media_url in media_urls:
        playback_id = _mux_playback_id(media_url)
        title = _title_near_playback_id(html, playback_id) if playback_id else None
        titles.append(title)
    return titles


def _entry_title(
    *,
    index: int,
    total: int,
    page_title: str | None,
    video_title: str | None,
    title_prefix: str,
) -> str:
    if video_title:
        cleaned = _clean_page_title(video_title)
        if page_title and total > 1 and cleaned.lower() not in (page_title or "").lower():
            return f"{page_title} - {cleaned}"
        return cleaned
    if page_title:
        if total > 1:
            return f"{page_title} - {index + 1}"
        return page_title
    return f"{title_prefix} {index + 1}"


def build_media_entries(
    media_urls: list[str],
    *,
    page_title: str | None = None,
    video_titles: list[str | None] | None = None,
    title_prefix: str = "Video",
    headers: dict[str, str] | None = None,
) -> list[dict]:
    entries: list[dict] = []
    for idx, media_url in enumerate(media_urls):
        lowered = media_url.lower()
        if ".m3u8" in lowered or "stream.mux.com" in lowered:
            ext = "m3u8"
        else:
            ext = "mp4"
        per_video_title = None
        if video_titles and idx < len(video_titles):
            per_video_title = video_titles[idx]
        title = _entry_title(
            index=idx,
            total=len(media_urls),
            page_title=page_title,
            video_title=per_video_title,
            title_prefix=title_prefix,
        )
        entry = {
            "title": title,
            "formats": [
                {
                    "format_id": "best",
                    "ext": ext,
                    "filesize_approx": "N/A",
                }
            ],
            "webpage_url": media_url,
        }
        if headers:
            entry["_ffmpeg_headers"] = dict(headers)
        entries.append(entry)
    return entries


@dataclass
class HlsVariant:
    bandwidth_kbps: int | None
    resolution: tuple[int, int] | None
    uri: str


@dataclass
class HlsScanResult:
    playlist_url: str
    headers: dict[str, str]
    variants: list[HlsVariant]


def scan_direct_m3u8(page_url: str, log: LogFn | None = None) -> HlsScanResult | None:
    logger = log or (lambda _msg: None)
    logger("[HLS] Scanning page for .m3u8 …")
    try:
        sess = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0)",
            "Referer": page_url,
        }
        html = sess.get(page_url, headers=headers, timeout=15).text

        iframe = re.search(r"<iframe[^>]+src=[\"']([^\"']+blazestreaming[^\"']+)[\"']", html, re.I)
        if iframe:
            iframe_url = urllib.parse.urljoin(page_url, iframe.group(1))
            html = sess.get(iframe_url, headers=headers, timeout=15).text
            
            parsed_url = urllib.parse.urlparse(iframe_url)
            qs = urllib.parse.parse_qs(parsed_url.query)
            video_id = qs.get("id", [""])[0]

            script_matches = re.findall(r"<script[^>]+src=[\"']([^\"']+\.js)[\"']", html, re.I)
            for script_src in script_matches:
                try:
                    script_url = urllib.parse.urljoin(iframe_url, script_src)
                    script_js = sess.get(script_url, headers=headers, timeout=15).text
                    html += "\n" + script_js
                except Exception:
                    pass

            if video_id:
                html = re.sub(r"'\s*\+\s*videoId\s*\+\s*'", video_id, html)
                html = re.sub(r"\"\s*\+\s*videoId\s*\+\s*\"", video_id, html)

        playlist_urls = _dedupe_preserve_order(M3U8_URL_RE.findall(html))
        if not playlist_urls:
            logger("[HLS] No playlist text found.")
            return None

        playlist_url = playlist_urls[0]
        logger(f"[HLS] Found playlist:\n{playlist_url}")
        if len(playlist_urls) > 1:
            logger(f"[HLS] Found {len(playlist_urls)} playlist(s) on page.")

        variants: list[HlsVariant] = []
        playlist = m3u8.load(playlist_url, headers=headers)
        if playlist.is_variant:
            logger("Available variants:")
            for variant in playlist.playlists:
                bandwidth = variant.stream_info.bandwidth
                bandwidth_kbps = bandwidth // 1000 if bandwidth else None
                resolution = variant.stream_info.resolution
                logger(f" • {bandwidth_kbps} kbps  {resolution}  ->  {variant.uri}")
                variants.append(
                    HlsVariant(
                        bandwidth_kbps=bandwidth_kbps,
                        resolution=resolution,
                        uri=variant.uri,
                    )
                )
        return HlsScanResult(playlist_url=playlist_url, headers=headers, variants=variants)
    except Exception as exc:
        logger(f"[HLS] {exc}")
        return None


def scan_direct_media_entries(page_url: str, log: LogFn | None = None) -> list[dict]:
    logger = log or (lambda _msg: None)
    logger("[Media] Scanning page for direct media URLs …")
    try:
        sess = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0)",
            "Referer": page_url,
        }
        html = sess.get(page_url, headers=headers, timeout=15).text

        iframe = re.search(r"<iframe[^>]+src=[\"']([^\"']+blazestreaming[^\"']+)[\"']", html, re.I)
        if iframe:
            iframe_url = urllib.parse.urljoin(page_url, iframe.group(1))
            html = sess.get(iframe_url, headers=headers, timeout=15).text

            parsed_url = urllib.parse.urlparse(iframe_url)
            qs = urllib.parse.parse_qs(parsed_url.query)
            video_id = qs.get("id", [""])[0]

            script_matches = re.findall(r"<script[^>]+src=[\"']([^\"']+\.js)[\"']", html, re.I)
            for script_src in script_matches:
                try:
                    script_url = urllib.parse.urljoin(iframe_url, script_src)
                    script_js = sess.get(script_url, headers=headers, timeout=15).text
                    html += "\n" + script_js
                except Exception:
                    pass

            if video_id:
                html = re.sub(r"'\s*\+\s*videoId\s*\+\s*'", video_id, html)
                html = re.sub(r"\"\s*\+\s*videoId\s*\+\s*\"", video_id, html)

        candidates: list[str] = []
        candidates.extend(M3U8_URL_RE.findall(html))
        candidates.extend(MUX_URL_RE.findall(html))
        candidates.extend(MP4_URL_RE.findall(html))
        media_urls = _dedupe_preserve_order(candidates)
        if not media_urls:
            logger("[Media] No direct media URLs found.")
            return []

        logger(f"[Media] Found {len(media_urls)} direct media URL(s).")
        for idx, media_url in enumerate(media_urls, start=1):
            logger(f" • {idx}: {media_url}")
        page_title = extract_page_title(html) or title_from_page_url(page_url)
        video_titles = guess_video_titles(html, media_urls)
        if page_title:
            logger(f"[Media] Page title: {page_title}")
        return build_media_entries(
            media_urls,
            page_title=page_title,
            video_titles=video_titles,
            headers=headers,
        )
    except Exception as exc:
        logger(f"[Media] {exc}")
        return []


def extract_embedded_page_videos(page_url: str, log: LogFn | None = None) -> list[dict]:
    logger = log or (lambda _msg: None)
    logger("[Media] Scanning page for embedded video links ...")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0)"}
        html = requests.get(page_url, headers=headers, timeout=15).text

        html = bytes(html, "utf-8").decode("unicode_escape", errors="ignore")

        candidates: list[str] = []
        og_matches = re.findall(
            r"<meta[^>]+property=\"og:video(?:[:_][^\"]+)?\"[^>]+content=\"([^\"]+)\"",
            html,
            re.I,
        )
        candidates.extend(og_matches)

        json_matches = re.findall(
            r"\"(?:progressiveUrl|playbackUrl)\":\"(https:[^\"]+?\.mp4[^\"]*)\"",
            html,
        )
        candidates.extend(json_matches)

        m3u8_matches = re.findall(r"(https:[^\"]+?\.m3u8[^\"]*)", html)
        candidates.extend(m3u8_matches)

        video_urls: list[str] = []
        for url in candidates:
            url = url.replace("\\u002F", "/")
            if url not in video_urls:
                video_urls.append(url)

        if not video_urls:
            logger("[Media] No direct video links found.")
            return []

        entries: list[dict] = []
        for idx, video_url in enumerate(video_urls):
            ext = "m3u8" if video_url.endswith(".m3u8") else "mp4"
            entries.append(
                {
                    "title": f"Embedded video {idx + 1}",
                    "formats": [
                        {
                            "format_id": "best",
                            "ext": ext,
                            "filesize_approx": "N/A",
                        }
                    ],
                    "webpage_url": video_url,
                }
            )
        return entries
    except Exception as exc:
        logger(f"[Media] {exc}")
        return []
