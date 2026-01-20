"""Non-UI extractors for special-case sites."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import re
import urllib.parse

import m3u8
import requests

LogFn = Callable[[str], None]


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

        iframe = re.search(r"<iframe[^>]+src=\"([^\"]+blazestreaming[^\"]+)\"", html, re.I)
        if iframe:
            iframe_url = urllib.parse.urljoin(page_url, iframe.group(1))
            html = sess.get(iframe_url, headers=headers, timeout=15).text

        match = re.search(r"https?://[^\"']+\.m3u8", html)
        if not match:
            logger("[HLS] No playlist text found.")
            return None

        playlist_url = match.group(0)
        logger(f"[HLS] Found playlist:\n{playlist_url}")

        variants: list[HlsVariant] = []
        playlist = m3u8.load(playlist_url, headers=headers)
        if playlist.is_variant:
            logger("Available variants:")
            for variant in playlist.playlists:
                bandwidth = variant.stream_info.bandwidth
                bandwidth_kbps = bandwidth // 1000 if bandwidth else None
                resolution = variant.stream_info.resolution
                logger(f" • {bandwidth_kbps} kbps  {resolution}  →  {variant.uri}")
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


def extract_linkedin_videos(page_url: str, log: LogFn | None = None) -> list[dict]:
    logger = log or (lambda _msg: None)
    if "linkedin.com" not in page_url:
        return []
    logger("[LinkedIn] Scanning page for direct video links …")
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
            logger("[LinkedIn] No direct video links found.")
            return []

        entries: list[dict] = []
        for idx, video_url in enumerate(video_urls):
            ext = "m3u8" if video_url.endswith(".m3u8") else "mp4"
            entries.append(
                {
                    "title": f"LinkedIn video {idx + 1}",
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
        logger(f"[LinkedIn] {exc}")
        return []
