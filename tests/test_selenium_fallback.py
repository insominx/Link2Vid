import unittest

from link2vid.core.selenium_fallback import (
    collapse_selenium_media_candidates,
    collect_media_urls_from_html,
    extract_media_url,
    is_media_url,
    normalize_scraped_url,
    pick_best_media_url,
    referer_for_media_url,
)
from tests.fixtures.hosts import AUTH_HOST_B


class TestSeleniumFallback(unittest.TestCase):
    def test_extract_media_url_prefers_m3u8(self):
        html = 'var src = "https://cdn.example.com/video/playlist.m3u8?token=abc";'
        self.assertEqual(extract_media_url(html), "https://cdn.example.com/video/playlist.m3u8?token=abc")

    def test_extract_media_url_falls_back_to_mp4(self):
        html = '{"url":"https://cdn.example.com/clips/lesson.mp4"}'
        self.assertEqual(extract_media_url(html), "https://cdn.example.com/clips/lesson.mp4")

    def test_extract_media_url_reads_og_video(self):
        html = '<meta property="og:video" content="https://cdn.example.com/preview.mp4">'
        self.assertEqual(extract_media_url(html), "https://cdn.example.com/preview.mp4")

    def test_collect_media_urls_finds_mux_stream(self):
        html = 'src="https://stream.mux.com/abc123.m3u8"'
        self.assertEqual(
            collect_media_urls_from_html(html),
            ["https://stream.mux.com/abc123.m3u8"],
        )

    def test_pick_best_media_url_prefers_hls(self):
        urls = [
            "https://cdn.example.com/video.mp4",
            "https://stream.mux.com/abc.m3u8",
        ]
        self.assertEqual(pick_best_media_url(urls), "https://stream.mux.com/abc.m3u8")

    def test_is_media_url_rejects_static_assets(self):
        self.assertFalse(is_media_url("https://cdn.example.com/app.js"))
        self.assertTrue(is_media_url("https://stream.mux.com/id.m3u8"))

    def test_normalize_scraped_url_decodes_json_escapes(self):
        escaped = "https://cdn.example.com/playlist.m3u8?a=1\\u0026b=2"
        self.assertEqual(
            normalize_scraped_url(escaped),
            "https://cdn.example.com/playlist.m3u8?a=1&b=2",
        )

    def test_pick_best_media_url_normalizes_escapes(self):
        urls = ["https://cdn.example.com/playlist.m3u8?token=abc\\u0026lang=en"]
        self.assertEqual(
            pick_best_media_url(urls),
            "https://cdn.example.com/playlist.m3u8?token=abc&lang=en",
        )

    def test_discover_media_urls_is_importable(self):
        from link2vid.core.selenium_fallback import discover_media_urls

        self.assertTrue(callable(discover_media_urls))

    def test_collapse_selenium_media_candidates_excludes_vimeo_player_urls(self):
        urls = [
            "https://player.vimeo.com/video/1189499785",
            "https://vod-adaptive-ak.vimeocdn.com/exp=123/37ceb782-ca2f-461d-8938-1142d1d8d7ff/sep/video/playlist.m3u8?omit=opus",
        ]

        collapsed = collapse_selenium_media_candidates(urls)

        self.assertEqual(len(collapsed), 1)
        self.assertIn("vimeocdn.com", collapsed[0])
        self.assertNotIn("player.vimeo.com", collapsed[0])

    def test_collapse_selenium_media_candidates_groups_vimeo_hls_asset_variants(self):
        urls = [
            "https://player.vimeo.com/video/1189499969",
            "https://vod-adaptive-ak.vimeocdn.com/exp=123/37ceb782-ca2f-461d-8938-1142d1d8d7ff/sep/video/playlist.m3u8?omit=opus",
            "https://skyfire.vimeocdn.com/exp=124/37ceb782-ca2f-461d-8938-1142d1d8d7ff/sep/video/playlist.m3u8?psid=abc&omit=av1-hevc-opus",
            "https://player.vimeo.com/video/1189499785",
            "https://vod-adaptive-ak.vimeocdn.com/exp=123/af7a0f07-6003-4a61-b588-0330a46de6f7/sep/video/playlist.m3u8?omit=opus",
            "https://skyfire.vimeocdn.com/exp=124/af7a0f07-6003-4a61-b588-0330a46de6f7/sep/video/playlist.m3u8?psid=def&omit=av1-hevc-opus",
            "https://player.vimeo.com/video/1189499888",
            "https://vod-adaptive-ak.vimeocdn.com/exp=123/44223761-968a-4b43-963c-b29b432bb63a/sep/video/playlist.m3u8?omit=opus",
            "https://skyfire.vimeocdn.com/exp=124/44223761-968a-4b43-963c-b29b432bb63a/sep/video/playlist.m3u8?psid=ghi&omit=av1-hevc-opus",
        ]

        collapsed = collapse_selenium_media_candidates(urls)

        self.assertEqual(len(collapsed), 3)
        self.assertTrue(all(".m3u8" in url for url in collapsed))
        self.assertEqual(
            [
                "37ceb782-ca2f-461d-8938-1142d1d8d7ff",
                "af7a0f07-6003-4a61-b588-0330a46de6f7",
                "44223761-968a-4b43-963c-b29b432bb63a",
            ],
            [url.split("/")[4] for url in collapsed],
        )

    def test_collapse_selenium_media_candidates_preserves_first_seen_group_order(self):
        urls = [
            "https://skyfire.vimeocdn.com/exp=124/af7a0f07-6003-4a61-b588-0330a46de6f7/sep/video/playlist.m3u8",
            "https://skyfire.vimeocdn.com/exp=124/37ceb782-ca2f-461d-8938-1142d1d8d7ff/sep/video/playlist.m3u8",
            "https://vod-adaptive-ak.vimeocdn.com/exp=123/af7a0f07-6003-4a61-b588-0330a46de6f7/sep/video/playlist.m3u8?omit=opus",
        ]

        collapsed = collapse_selenium_media_candidates(urls)

        self.assertEqual(
            [
                "af7a0f07-6003-4a61-b588-0330a46de6f7",
                "37ceb782-ca2f-461d-8938-1142d1d8d7ff",
            ],
            [url.split("/")[4] for url in collapsed],
        )

    def test_collapse_selenium_media_candidates_preserves_generic_direct_urls(self):
        urls = [
            "https://cdn.example.com/video.mp4",
            "https://stream.mux.com/abc123.m3u8",
            "https://cdn.example.com/video.webm",
        ]

        self.assertEqual(
            collapse_selenium_media_candidates(urls),
            urls,
        )

    def test_collapse_selenium_media_candidates_returns_empty_for_player_only(self):
        self.assertEqual(
            collapse_selenium_media_candidates(["https://player.vimeo.com/video/1189499785"]),
            [],
        )

    def test_referer_for_media_url_uses_vimeo_player(self):
        self.assertEqual(
            referer_for_media_url(
                "https://skyfire.vimeocdn.com/abc/playlist.m3u8",
                page_url=f"https://{AUTH_HOST_B}/c/foo",
            ),
            "https://player.vimeo.com/",
        )

    def test_referer_for_media_url_uses_page_for_other_sites(self):
        page = f"https://{AUTH_HOST_B}/c/foo"
        self.assertEqual(
            referer_for_media_url(
                "https://stream.mux.com/abc.m3u8",
                page_url=page,
            ),
            page,
        )


if __name__ == "__main__":
    unittest.main()
