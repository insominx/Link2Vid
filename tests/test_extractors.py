import unittest
from unittest.mock import MagicMock, patch

import requests

from link2vid.core.extractors import (
    build_media_entries,
    extract_page_title,
    resolve_blazestreaming_playback_url,
    scan_direct_media_entries,
    title_from_page_url,
)
from tests.fixtures.hosts import AUTH_HOST_B


class TestExtractors(unittest.TestCase):
    def test_build_media_entries_assigns_titles(self):
        entries = build_media_entries(
            [
                "https://stream.mux.com/abc.m3u8",
                "https://cdn.example.com/lesson.mp4",
            ]
        )
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["title"], "Video 1")
        self.assertEqual(entries[1]["title"], "Video 2")
        self.assertEqual(entries[0]["webpage_url"], "https://stream.mux.com/abc.m3u8")
        self.assertEqual(entries[1]["formats"][0]["ext"], "mp4")

    def test_build_media_entries_uses_page_title(self):
        entries = build_media_entries(
            ["https://stream.mux.com/abc.m3u8"],
            page_title="Bonus Molly Mahoney's Vivid Visual Trio",
        )
        self.assertEqual(entries[0]["title"], "Bonus Molly Mahoney's Vivid Visual Trio")

    def test_build_media_entries_numbers_multiple_videos_from_page_title(self):
        entries = build_media_entries(
            [
                "https://stream.mux.com/one.m3u8",
                "https://stream.mux.com/two.m3u8",
            ],
            page_title="Bonus Molly Mahoney's Vivid Visual Trio",
        )
        self.assertEqual(entries[0]["title"], "Bonus Molly Mahoney's Vivid Visual Trio - 1")
        self.assertEqual(entries[1]["title"], "Bonus Molly Mahoney's Vivid Visual Trio - 2")

    def test_build_media_entries_uses_discovered_video_titles(self):
        entries = build_media_entries(
            ["https://stream.mux.com/abc123.m3u8"],
            page_title="Workshop Replay",
            video_titles=["Prompt Engineering Deep Dive"],
        )
        self.assertEqual(entries[0]["title"], "Prompt Engineering Deep Dive")

    def test_build_media_entries_preserves_headers(self):
        headers = {"Referer": f"https://{AUTH_HOST_B}/c/foo"}
        entries = build_media_entries(["https://stream.mux.com/abc.m3u8"], headers=headers)
        self.assertEqual(entries[0]["_ffmpeg_headers"], headers)

    def test_extract_page_title_reads_og_title(self):
        html = """
        <html>
          <head>
            <meta property="og:title" content="Bonus: Molly Mahoney's Vivid Visual Trio | Member Area">
          </head>
        </html>
        """
        self.assertEqual(
            extract_page_title(html),
            "Bonus: Molly Mahoney's Vivid Visual Trio",
        )

    def test_title_from_page_url_uses_slug(self):
        url = f"https://{AUTH_HOST_B}/c/resource-library-fd9bd0/bonus-molly-mahoney-s-vivid-visual-trio"
        self.assertEqual(
            title_from_page_url(url),
            "Bonus Molly Mahoney S Vivid Visual Trio",
        )

    @patch("link2vid.core.extractors.requests.Session")
    def test_scan_direct_media_entries_finds_multiple_playlists(self, session_cls):
        session = session_cls.return_value
        response = session.get.return_value
        response.text = """
        <html>
          <head>
            <title>Bonus: Molly Mahoney's Vivid Visual Trio | AI Advantage Club</title>
          </head>
          <body>
            <script>
              const one = "https://stream.mux.com/video-one.m3u8";
              const two = "https://stream.mux.com/video-two.m3u8";
              const three = "https://stream.mux.com/video-three.m3u8";
            </script>
          </body>
        </html>
        """
        url = f"https://{AUTH_HOST_B}/c/resource-library-fd9bd0/bonus-molly-mahoney-s-vivid-visual-trio"
        entries = scan_direct_media_entries(url)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["title"], "Bonus: Molly Mahoney's Vivid Visual Trio - 1")
        self.assertEqual(entries[2]["title"], "Bonus: Molly Mahoney's Vivid Visual Trio - 3")


GDC_PAGE_URL = "https://gdcvault.com/play/1035971/Teaching-While"
GDC_PLAYER_ID = "5ff208f0041347baa01beeea3b2ca0ac"
GDC_PAGE_HTML = f"""
<html>
  <head>
    <meta property="og:title" content="Teaching While Gamedev" />
    <title>GDC Vault - Teaching While Gamedev</title>
  </head>
  <body>
    <iframe src="https://gdcvault.blazestreaming.com/?id={GDC_PLAYER_ID}" width="600"></iframe>
  </body>
</html>
"""
GDC_TAIL = "/38549d9a185b445888c6fbc2e2e792e9/50413c5b8a95441e919f0a1579606e47/index.m3u8"
GDC_LEGACY_URL = f"https://cdn-a.blazestreaming.com/out/v1/{GDC_PLAYER_ID}{GDC_TAIL}"
GDC_ALIAS_URL = f"https://cdn-a.blazestreaming.com/out/v1/88797a82bc4f4396bf37616fe9811996{GDC_TAIL}"


def _gdc_session(session_cls, aliases_payload, *, alias_status=200):
    page = MagicMock(text=GDC_PAGE_HTML)
    aliases = MagicMock(status_code=alias_status)
    aliases.json.return_value = aliases_payload

    def fake_get(url, **_kwargs):
        return aliases if url.endswith("/aliases.json") else page

    session = session_cls.return_value
    session.get.side_effect = fake_get
    return session


class TestBlazestreamingResolution(unittest.TestCase):
    @patch("link2vid.core.extractors.requests.Session")
    def test_gdc_vault_page_uses_alias_playback_url(self, session_cls):
        _gdc_session(
            session_cls,
            {"schemaVersion": 1, "aliases": {GDC_PLAYER_ID: {"generation": 1, "playbackUrl": GDC_ALIAS_URL}}},
        )
        entries = scan_direct_media_entries(GDC_PAGE_URL)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["webpage_url"], GDC_ALIAS_URL)
        self.assertEqual(entries[0]["formats"][0]["ext"], "m3u8")
        self.assertEqual(entries[0]["title"], "Teaching While Gamedev")

    @patch("link2vid.core.extractors.requests.Session")
    def test_gdc_vault_unmapped_id_falls_back_to_id_derived_url(self, session_cls):
        _gdc_session(session_cls, {"schemaVersion": 1, "aliases": {}})
        entries = scan_direct_media_entries(GDC_PAGE_URL)
        self.assertEqual([entry["webpage_url"] for entry in entries], [GDC_LEGACY_URL])

    @patch("link2vid.core.extractors.requests.Session")
    def test_gdc_vault_rejects_alias_outside_blazestreaming_cdn(self, session_cls):
        _gdc_session(
            session_cls,
            {"aliases": {GDC_PLAYER_ID: {"playbackUrl": "https://evil.example.com/index.m3u8"}}},
        )
        entries = scan_direct_media_entries(GDC_PAGE_URL)
        self.assertEqual([entry["webpage_url"] for entry in entries], [GDC_LEGACY_URL])

    def test_gdc_vault_unreachable_aliases_falls_back(self):
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("down")
        playback_url = resolve_blazestreaming_playback_url(session, GDC_PAGE_URL, GDC_PAGE_HTML, {})
        self.assertEqual(playback_url, GDC_LEGACY_URL)

    def test_page_without_blazestreaming_iframe_is_not_resolved(self):
        session = MagicMock()
        self.assertIsNone(resolve_blazestreaming_playback_url(session, GDC_PAGE_URL, "<html></html>", {}))
        session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
