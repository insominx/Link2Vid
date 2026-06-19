import unittest
from unittest.mock import patch

from link2vid.core.extractors import (
    build_media_entries,
    extract_page_title,
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


if __name__ == "__main__":
    unittest.main()
