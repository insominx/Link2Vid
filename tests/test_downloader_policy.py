import unittest
from unittest.mock import patch

from doggybag.core.downloader import DownloadManager, TranscriptTrack
from doggybag.core.errors import CookiesRequiredError, NoTranscriptAvailableError
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
        self.assertTrue(
            manager._should_try_browser_cookies(
                f"https://{VIDEO_HOST_A}/watch/1",
                Exception("Failed to decrypt with DPAPI"),
            )
        )
        self.assertFalse(manager._should_try_browser_cookies(f"https://{VIDEO_HOST_A}/watch/1", Exception("timeout")))

    def test_media_403_is_not_treated_as_an_auth_signal(self):
        """A 403 on the media stream means PO Token/throttling, not missing cookies.

        Escalating it to the browser-cookie ladder wastes an attempt per installed
        browser and reports a misleading "cookies/auth" cause to the user.
        """
        manager = DownloadManager(ydl_logger=DummyLogger())
        for message in (
            "unable to download video data: HTTP Error 403: Forbidden",
            "HTTP Error 403: Forbidden",
        ):
            self.assertFalse(
                manager._should_try_browser_cookies(f"https://{VIDEO_HOST_A}/watch/1", Exception(message)),
                msg=message,
            )

    def test_should_not_try_browser_cookies_for_non_auth_error(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        self.assertFalse(
            manager._should_try_browser_cookies(
                f"https://{VIDEO_HOST_A}/status/1",
                Exception("No video could be found on this page"),
            )
        )

    def test_cookie_failure_hint_covers_missing_edge_profile(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        hint = manager._cookie_failure_hint(
            Exception('could not find edge cookies database in "C:\\Users\\me\\AppData\\Local\\Microsoft\\Edge\\User Data"')
        )
        self.assertIsNotNone(hint)
        self.assertIn("Edge profile not found", hint)

    def test_cookie_access_errors_keep_original_site_error(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        first_err = Exception("unable to download video data: HTTP Error 403: Forbidden")
        access_err = Exception(
            "could not find firefox cookies database in "
            "'C:\\\\Users\\\\mlinh\\\\AppData\\\\Roaming\\\\Mozilla\\\\Firefox\\\\Profiles'"
        )
        self.assertTrue(manager._is_cookie_access_error(access_err))
        self.assertFalse(manager._is_cookie_access_error(first_err))
        self.assertIs(
            manager._note_browser_cookie_failure("firefox", access_err, first_err),
            first_err,
        )

    def test_site_auth_retry_error_replaces_original(self):
        manager = DownloadManager(ydl_logger=DummyLogger())
        first_err = Exception("unable to download video data: HTTP Error 403: Forbidden")
        site_err = Exception("Sign in to confirm you’re not a bot")
        self.assertFalse(manager._is_cookie_access_error(site_err))
        self.assertIs(
            manager._note_browser_cookie_failure("brave", site_err, first_err),
            site_err,
        )

    def test_download_preserves_original_error_when_cookie_access_fails(self):
        first_err = Exception("Sign in to confirm you're not a bot")
        access_err = Exception("could not find firefox cookies database in Profiles")

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                if self.opts.get("cookiesfrombrowser"):
                    raise access_err
                raise first_err

        manager = DownloadManager(ydl_logger=DummyLogger())
        manager._browser_candidates = lambda: ["firefox"]
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            with self.assertRaises(CookiesRequiredError) as ctx:
                manager.download(f"https://{VIDEO_HOST_A}/watch/1", "best", "out")
        self.assertIs(ctx.exception.original, first_err)
        self.assertEqual(manager.last_cookies_mode, "none")
        self.assertIsNone(manager.last_cookies_browser)

    def test_media_403_falls_back_through_youtube_player_clients(self):
        """YouTube caps unverified clients at ~1 MiB, then 403s every later range.

        An embedded client often still serves the whole file, so a media 403 must
        walk the alternatives instead of failing or blaming cookies.
        """
        seen = []

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                client = (self.opts.get("extractor_args", {}).get("youtube", {}).get("player_client") or [None])[0]
                seen.append(client)
                if client == "web_embedded":
                    return None
                raise Exception("unable to download video data: HTTP Error 403: Forbidden")

        manager = DownloadManager(ydl_logger=DummyLogger())
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            self.assertTrue(manager.download("https://www.youtube.com/watch?v=abc", "best", "out"))
        self.assertEqual(seen, [None, "web_embedded"])
        self.assertEqual(manager.last_player_client, "web_embedded")

    def test_media_403_does_not_switch_clients_for_non_youtube_sites(self):
        seen = []

        class FakeYoutubeDL:
            def __init__(self, opts):
                seen.append(opts.get("extractor_args"))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                raise Exception("unable to download video data: HTTP Error 403: Forbidden")

        manager = DownloadManager(ydl_logger=DummyLogger())
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            self.assertFalse(manager.download(f"https://{VIDEO_HOST_A}/watch/1", "best", "out"))
        self.assertEqual(seen, [None])

    def test_download_extract_audio_adds_mp3_postprocessor(self):
        captured = {}

        class FakeYoutubeDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                return None

        manager = DownloadManager(ydl_logger=DummyLogger())
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            self.assertTrue(
                manager.download(f"https://{VIDEO_HOST_A}/watch/1", "bestaudio", "out", extract_audio=True)
            )
        self.assertEqual(captured["format"], "best[acodec!=none]/best/bestaudio")
        self.assertEqual(captured["postprocessors"][0]["key"], "FFmpegExtractAudio")
        self.assertEqual(captured["postprocessors"][0]["preferredcodec"], "mp3")

    def test_download_extract_audio_retries_muxed_after_bestaudio_403(self):
        seen = []

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                seen.append(self.opts.get("format"))
                if self.opts.get("format") != "best":
                    raise Exception("unable to download video data: HTTP Error 403: Forbidden")
                return None

        manager = DownloadManager(ydl_logger=DummyLogger())
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            self.assertTrue(
                manager.download(f"https://{VIDEO_HOST_A}/watch/1", "bestaudio", "out", extract_audio=True)
            )
        self.assertIn("best", seen)
        self.assertEqual(seen[-1], "best")

    def test_download_without_extract_audio_leaves_raw_format(self):
        captured = {}

        class FakeYoutubeDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                return None

        manager = DownloadManager(ydl_logger=DummyLogger())
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            self.assertTrue(manager.download(f"https://{VIDEO_HOST_A}/watch/1", "bestaudio", "out"))
        self.assertEqual(captured["format"], "bestaudio")
        self.assertNotIn("postprocessors", captured)

    def test_first_attempt_is_cookieless_even_with_a_preferred_browser(self):
        """Browser cookies are an escalation, not the default.

        Reading them is slow, fails outright while the browser is running, and is
        impossible on Chromium browsers with App-Bound Encryption. A public video
        must not pay that cost, so the first attempt carries no cookies.
        """
        captured = {}

        class FakeYoutubeDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                return None

        manager = DownloadManager(
            ydl_logger=DummyLogger(),
            get_cookies_browser=lambda: "brave",
        )
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            self.assertTrue(manager.download(f"https://{VIDEO_HOST_A}/watch/1", "best", "out"))
        self.assertNotIn("cookiesfrombrowser", captured)
        self.assertEqual(manager.last_cookies_mode, "none")
        self.assertIsNone(manager.last_cookies_browser)

    def test_preferred_browser_dpapi_falls_through_to_other_browser(self):
        dpapi_err = Exception("Failed to decrypt with DPAPI")
        seen = []

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                browser = (self.opts.get("cookiesfrombrowser") or (None,))[0]
                seen.append(browser)
                if browser is None:
                    raise Exception("Sign in to confirm you're not a bot")
                if browser == "chrome":
                    raise dpapi_err
                if browser == "brave":
                    return None
                raise Exception("Sign in to confirm you're not a bot")

        manager = DownloadManager(
            ydl_logger=DummyLogger(),
            get_cookies_browser=lambda: "chrome",
        )
        manager._browser_candidates = lambda: ["chrome", "brave", "edge"]
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            self.assertTrue(manager.download(f"https://{VIDEO_HOST_A}/watch/1", "best", "out"))
        # Cookieless first, then the preferred browser, then the rest.
        self.assertEqual(seen, [None, "chrome", "brave"])
        self.assertEqual(manager.last_cookies_browser, "brave")

    def test_undecryptable_browsers_still_surface_the_site_error(self):
        """Chromium App-Bound Encryption makes browser cookies unreadable.

        When every candidate fails that way the user must still see why the site
        refused, not a cookie-plumbing error they cannot act on.
        """
        site_err = Exception("Sign in to confirm you're not a bot")
        dpapi_err = Exception("Failed to decrypt with DPAPI")
        seen = []

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                browser = (self.opts.get("cookiesfrombrowser") or (None,))[0]
                seen.append(browser)
                if browser:
                    raise dpapi_err
                raise site_err

        manager = DownloadManager(
            ydl_logger=DummyLogger(),
            get_cookies_browser=lambda: "chrome",
        )
        manager._browser_candidates = lambda: ["chrome", "brave"]
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            with self.assertRaises(CookiesRequiredError) as ctx:
                manager.download(f"https://{VIDEO_HOST_A}/watch/1", "best", "out")
        self.assertEqual(seen, [None, "chrome", "brave"])
        self.assertIs(ctx.exception.original, site_err)
        self.assertEqual(manager.last_cookies_mode, "none")
        self.assertIsNone(manager.last_cookies_browser)

    def test_cookies_txt_overrides_preferred_browser(self):
        captured = {}

        class FakeYoutubeDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                return None

        manager = DownloadManager(
            ydl_logger=DummyLogger(),
            get_cookies_path=lambda: "C:\\cookies.txt",
            get_cookies_browser=lambda: "brave",
        )
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            self.assertTrue(manager.download(f"https://{VIDEO_HOST_A}/watch/1", "best", "out"))
        self.assertEqual(captured["cookiefile"], "C:\\cookies.txt")
        self.assertNotIn("cookiesfrombrowser", captured)
        self.assertEqual(manager.last_cookies_mode, "cookies.txt")

    def test_download_marks_browser_cookies_only_after_success(self):
        first_err = Exception("Sign in to confirm you're not a bot")

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                if self.opts.get("cookiesfrombrowser") == ("brave",):
                    return None
                raise first_err

        manager = DownloadManager(ydl_logger=DummyLogger())
        manager._browser_candidates = lambda: ["brave"]
        with patch("doggybag.core.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            self.assertTrue(manager.download(f"https://{VIDEO_HOST_A}/watch/1", "best", "out"))
        self.assertEqual(manager.last_cookies_mode, "browser")
        self.assertEqual(manager.last_cookies_browser, "brave")

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
