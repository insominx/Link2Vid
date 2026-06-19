import unittest
from unittest.mock import patch

from link2vid.core.downloader import DownloadManager, TranscriptTrack
from link2vid.core.errors import NoTranscriptAvailableError
from tests.fixtures.hosts import VIDEO_HOST_A


class DummyLogger:
    def debug(self, _msg):
        return None

    def warning(self, _msg):
        return None

    def error(self, _msg):
        return None


class TestDownloaderPolicy(unittest.TestCase):
    def test_browser_candidates_ordering(self):
        manager = DownloadManager(ydl_logger=DummyLogger(), dev_defaults={"cookies_browser": "brave"})
        with patch.object(manager, "_running_browsers", return_value=["chrome", "edge"]):
            candidates = manager._browser_candidates()
        self.assertEqual(candidates, ["brave", "chrome", "edge", "firefox"])

    def test_should_try_browser_cookies(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        self.assertTrue(manager._should_try_browser_cookies(f"https://{VIDEO_HOST_A}/vid", Exception("login required")))
        self.assertTrue(manager._should_try_browser_cookies(f"https://{VIDEO_HOST_A}/watch/1", Exception("HTTP Error 403")))
        self.assertFalse(manager._should_try_browser_cookies(f"https://{VIDEO_HOST_A}/watch/1", Exception("timeout")))

    def test_should_not_try_browser_cookies_for_non_auth_error(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        self.assertFalse(
            manager._should_try_browser_cookies(
                f"https://{VIDEO_HOST_A}/status/1",
                Exception("No video could be found on this page"),
            )
        )

    def test_apply_js_runtime_opts_node(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        opts = {}
        with patch.object(manager, "_select_js_runtime", return_value=("node", "C:\\node.exe")):
            manager._apply_js_runtime_opts(opts)
        self.assertEqual(manager.last_js_runtime, "node")
        self.assertIn("js_runtimes", opts)
        self.assertIn("remote_components", opts)
        self.assertEqual(opts["js_runtimes"]["node"]["path"], "C:\\node.exe")
        self.assertEqual(opts["remote_components"], ["ejs:github"])

    def test_apply_js_runtime_opts_deno_adds_npm(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        opts = {}
        with patch.object(manager, "_select_js_runtime", return_value=("deno", "/usr/bin/deno")):
            manager._apply_js_runtime_opts(opts)
        self.assertEqual(opts["remote_components"], ["ejs:github", "ejs:npm"])

    def test_transcript_config_prefers_manual_subtitles(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        config = manager._transcript_download_config(
            {
                "subtitles": {"en": [{"ext": "vtt"}], "live_chat": [{"ext": "json"}]},
                "automatic_captions": {"fr": [{"ext": "vtt"}]},
            }
        )
        self.assertEqual(config["source"], "subtitles")
        self.assertEqual(config["languages"], ["en"])
        self.assertEqual(config["available_languages"], ["en"])
        self.assertTrue(config["writesubtitles"])
        self.assertFalse(config["writeautomaticsub"])

    def test_transcript_config_prefers_english_manual_subtitle_when_multiple_exist(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        config = manager._transcript_download_config(
            {
                "subtitles": {"aa": [{"ext": "vtt"}], "en": [{"ext": "vtt"}], "es": [{"ext": "vtt"}]},
                "automatic_captions": {},
            }
        )
        self.assertEqual(config["source"], "subtitles")
        self.assertEqual(config["languages"], ["en"])
        self.assertEqual(config["available_languages"], ["aa", "en", "es"])

    def test_transcript_config_uses_original_language_when_english_missing(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        config = manager._transcript_download_config(
            {
                "language": "de",
                "subtitles": {"de": [{"ext": "vtt"}], "fr": [{"ext": "vtt"}]},
                "automatic_captions": {},
            }
        )
        self.assertEqual(config["languages"], ["de"])

    def test_transcript_config_falls_back_to_automatic_captions(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        config = manager._transcript_download_config(
            {
                "subtitles": {},
                "automatic_captions": {"en": [{"ext": "vtt"}], "es": [{"ext": "vtt"}]},
            }
        )
        self.assertEqual(config["source"], "automatic captions")
        self.assertEqual(config["languages"], ["en"])
        self.assertEqual(config["available_languages"], ["en", "es"])
        self.assertFalse(config["writesubtitles"])
        self.assertTrue(config["writeautomaticsub"])

    def test_transcript_config_raises_when_no_tracks_exist(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        with self.assertRaises(NoTranscriptAvailableError):
            manager._transcript_download_config({"subtitles": {}, "automatic_captions": {}})

    def test_transcript_tracks_include_manual_and_automatic_languages(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        tracks = manager.transcript_tracks_from_info(
            {
                "subtitles": {
                    "es": [{"ext": "vtt", "name": "Spanish"}],
                    "live_chat": [{"ext": "json"}],
                },
                "automatic_captions": {
                    "en": [{"ext": "vtt", "name": "English"}],
                    "th-orig": [{"ext": "vtt", "name": "Thai (Original)"}],
                },
            }
        )

        self.assertEqual(
            tracks,
            [
                TranscriptTrack(source="subtitles", language="es", label="Spanish (es) - Subtitles"),
                TranscriptTrack(source="automatic captions", language="en", label="English (en) - Auto"),
                TranscriptTrack(source="automatic captions", language="th-orig", label="Thai (Original) (th-orig) - Auto"),
            ],
        )

    def test_transcript_tracks_are_sorted_by_label(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        tracks = manager.transcript_tracks_from_info(
            {
                "language": "th",
                "subtitles": {},
                "automatic_captions": {
                    "ab": [{"ext": "vtt", "name": "Abkhazian"}],
                    "en": [{"ext": "vtt", "name": "English"}],
                    "th-orig": [{"ext": "vtt", "name": "Thai (Original)"}],
                },
            }
        )

        self.assertEqual([track.language for track in tracks], ["ab", "en", "th-orig"])

    def test_transcript_config_uses_explicit_automatic_caption_language(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        config = manager._transcript_download_config(
            {
                "subtitles": {"en": [{"ext": "vtt"}]},
                "automatic_captions": {"th-orig": [{"ext": "vtt"}]},
            },
            selected_track=TranscriptTrack(
                source="automatic captions",
                language="th-orig",
                label="Thai (Original) (th-orig) - Auto",
            ),
        )

        self.assertEqual(config["source"], "automatic captions")
        self.assertEqual(config["languages"], ["th-orig"])
        self.assertFalse(config["writesubtitles"])
        self.assertTrue(config["writeautomaticsub"])

    def test_transcript_config_rejects_unavailable_explicit_language(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        with self.assertRaises(NoTranscriptAvailableError):
            manager._transcript_download_config(
                {"subtitles": {"en": [{"ext": "vtt"}]}, "automatic_captions": {}},
                selected_track=TranscriptTrack(source="subtitles", language="fr", label="French (fr) - Subtitles"),
            )


if __name__ == "__main__":
    unittest.main()
