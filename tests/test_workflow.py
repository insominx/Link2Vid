"""Shared synchronous workflow used by the GUI controller and the headless CLI."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from doggybag.app import workflow
from doggybag.app.controller import AppController, _ControllerPrompter
from doggybag.core.errors import CookiesRequiredError, NoTranscriptAvailableError
from doggybag.core.extractors import HlsScanResult
from doggybag.core.fetcher import (
    DirectHlsFound,
    FetchError,
    FetchResults,
    NeedsCookies,
    NeedsLogin,
    NeedsSelenium,
)
from tests.fixtures.hosts import VIDEO_HOST_A

URL = f"https://{VIDEO_HOST_A}/watch/1"


class ScriptedPrompter:
    def __init__(self, *, cookies=False, fallback=False, ffmpeg="cancel", creds=(None, None), browser=False):
        self.answers = {
            "cookie_retry": cookies,
            "format_fallback": fallback,
            "ffmpeg_missing": ffmpeg,
            "credentials": creds,
            "allow_browser_automation": browser,
        }
        self.calls: list[str] = []

    def cookie_retry(self):
        self.calls.append("cookie_retry")
        return self.answers["cookie_retry"]

    def format_fallback(self, *, extract_audio):
        self.calls.append("format_fallback")
        return self.answers["format_fallback"]

    def ffmpeg_missing(self, fmt_label):
        self.calls.append("ffmpeg_missing")
        return self.answers["ffmpeg_missing"]

    def credentials(self, url, *, browser_automation):
        self.calls.append(f"credentials:{browser_automation}")
        return self.answers["credentials"]

    def allow_browser_automation(self):
        self.calls.append("allow_browser_automation")
        return self.answers["allow_browser_automation"]


class SequenceFetcher:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple] = []

    def fetch(self, url, username=None, password=None):
        self.calls.append((url, username, password))
        return self.outcomes.pop(0)


class _Logs:
    def setUp(self) -> None:
        self.lines: list[str] = []
        self.errors: list[tuple[str, Exception]] = []

    def log(self, message):
        self.lines.append(message)

    def log_error(self, stage, err):
        self.errors.append((stage, err))


class TestResolveFetch(_Logs, unittest.TestCase):
    def resolve(self, fetcher, prompter, **kwargs):
        return workflow.resolve_fetch(
            URL,
            fetcher=fetcher,
            prompter=prompter,
            log=self.log,
            log_error=self.log_error,
            dev_defaults={},
            **kwargs,
        )

    def test_results_pass_through_and_log_partial_error(self) -> None:
        err = Exception("yt-dlp failed")
        result = self.resolve(SequenceFetcher(FetchResults([{"title": "a"}], error=err)), ScriptedPrompter())
        self.assertEqual(result.entries, [{"title": "a"}])
        self.assertEqual(result.source, "page-scan")
        self.assertEqual(self.errors, [("yt-dlp", err)])

    def test_needs_cookies_declined_returns_empty_declined_cookies(self) -> None:
        prompter = ScriptedPrompter(cookies=False)
        result = self.resolve(SequenceFetcher(NeedsCookies(CookiesRequiredError())), prompter)
        self.assertEqual(result.entries, [])
        self.assertEqual(result.declined, "cookies")
        self.assertEqual(prompter.calls, ["cookie_retry"])

    def test_needs_cookies_refetches_once_then_stops(self) -> None:
        fetcher = SequenceFetcher(NeedsCookies(Exception("a")), NeedsCookies(Exception("b")))
        result = self.resolve(fetcher, ScriptedPrompter(cookies=True))
        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(result.declined, "cookies")
        self.assertIn("Browser cookie retry did not resolve the fetch error.", self.lines)

    def test_needs_login_uses_prompter_credentials_then_refetches(self) -> None:
        fetcher = SequenceFetcher(NeedsLogin(Exception("login")), FetchResults([{"title": "t"}]))
        prompter = ScriptedPrompter(creds=("me", "pw"))
        result = self.resolve(fetcher, prompter)
        self.assertEqual(fetcher.calls[1], (URL, "me", "pw"))
        self.assertEqual(prompter.calls, ["credentials:False"])
        self.assertEqual(len(result.entries), 1)

    def test_needs_login_without_credentials_declines(self) -> None:
        result = self.resolve(SequenceFetcher(NeedsLogin(Exception("login"))), ScriptedPrompter())
        self.assertEqual(result.declined, "credentials")
        self.assertIn("No credentials provided.", self.lines)

    def test_direct_hls_builds_one_direct_entry_with_page_title_and_headers(self) -> None:
        scan = HlsScanResult(
            playlist_url=f"https://{VIDEO_HOST_A}/master.m3u8",
            headers={"Referer": URL},
            variants=[],
        )
        result = self.resolve(SequenceFetcher(DirectHlsFound(scan)), ScriptedPrompter())
        self.assertEqual(result.source, "hls-scan")
        self.assertEqual(len(result.entries), 1)
        self.assertTrue(workflow.is_direct_entry(result.entries[0]))
        self.assertEqual(result.entries[0]["_ffmpeg_headers"], {"Referer": URL})

    def test_needs_selenium_requires_permission_before_credentials_or_browser(self) -> None:
        selenium = mock.Mock(return_value=[{"title": "x"}])
        prompter = ScriptedPrompter(browser=False, creds=("me", "pw"))
        result = self.resolve(SequenceFetcher(NeedsSelenium(Exception("auth"))), prompter, selenium_fetch=selenium)
        self.assertEqual(result.declined, "browser")
        self.assertEqual(prompter.calls, ["allow_browser_automation"])
        selenium.assert_not_called()

        prompter = ScriptedPrompter(browser=True, creds=("me", "pw"))
        result = self.resolve(SequenceFetcher(NeedsSelenium(None)), prompter, selenium_fetch=selenium)
        self.assertEqual(result.source, "browser")
        self.assertEqual(prompter.calls, ["allow_browser_automation", "credentials:True"])
        selenium.assert_called_once_with(URL, "me", "pw")

    def test_selenium_empty_result_logs_and_reports_browser_tried(self) -> None:
        prompter = ScriptedPrompter(browser=True, creds=("me", "pw"))
        result = self.resolve(
            SequenceFetcher(NeedsSelenium(None)), prompter, selenium_fetch=mock.Mock(return_value=[])
        )
        self.assertTrue(result.browser_tried)
        self.assertEqual(result.entries, [])
        self.assertIn("No media found with Selenium.", self.lines)

    def test_fetch_error_logs_fetch_stage(self) -> None:
        err = Exception("no video")
        result = self.resolve(SequenceFetcher(FetchError(err)), ScriptedPrompter())
        self.assertEqual(self.errors, [("Fetch", err)])
        self.assertIs(result.error, err)

    def test_source_labels(self) -> None:
        result = self.resolve(SequenceFetcher(FetchResults([{"title": "a"}])), ScriptedPrompter())
        self.assertEqual(result.source, "yt-dlp")


class TestDownloadMedia(_Logs, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name
        self.manager = mock.Mock()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_download(self, format_id="bestvideo+bestaudio/best", prompter=None, **kwargs):
        return workflow.download_media(
            self.manager,
            URL,
            format_id,
            self.folder,
            prompter=prompter or ScriptedPrompter(),
            log=self.log,
            log_error=self.log_error,
            **kwargs,
        )

    def writer(self, name="video.mp4", *, report=True):
        def download(url, selector, outtmpl, progress_hook=None, post_hook=None, **kwargs):
            path = Path(self.folder) / name
            path.write_bytes(b"data")
            if report:
                post_hook(str(path))
            return True

        return download

    def test_success_reports_exact_outputs_from_post_hook(self) -> None:
        self.manager.download.side_effect = self.writer()
        result = self.run_download()
        self.assertTrue(result.ok)
        self.assertTrue(result.exact)
        self.assertEqual([output.path.name for output in result.outputs], ["video.mp4"])
        self.assertFalse(result.already_existed)

    def test_folder_scan_outputs_are_not_exact(self) -> None:
        self.manager.download.side_effect = self.writer(report=False)
        result = self.run_download()
        self.assertTrue(result.ok)
        self.assertFalse(result.exact)

    def test_preexisting_output_reports_already_existed(self) -> None:
        path = Path(self.folder) / "old.mp4"
        path.write_bytes(b"data")
        old = time.time() - 60
        os.utime(path, (old, old))

        def download(url, selector, outtmpl, progress_hook=None, post_hook=None, **kwargs):
            post_hook(str(path))
            return True

        self.manager.download.side_effect = download
        self.assertTrue(self.run_download().already_existed)

    def test_cookie_retry_runs_once_when_prompter_agrees(self) -> None:
        self.manager.download.side_effect = [CookiesRequiredError(), self.writer()(URL, "", "", post_hook=lambda p: None)]
        result = self.run_download(prompter=ScriptedPrompter(cookies=True))
        self.assertEqual(self.manager.download.call_count, 2)
        self.assertTrue(result.ok)

    def test_cookie_decline_returns_failure_with_cookie_error(self) -> None:
        self.manager.download.side_effect = CookiesRequiredError(original=Exception("Sign in"))
        result = self.run_download()
        self.assertFalse(result.ok)
        self.assertIsInstance(result.error, CookiesRequiredError)
        self.assertEqual(self.manager.download.call_count, 1)

    def test_format_unavailable_falls_back_to_best(self) -> None:
        unavailable = Exception("ERROR: Requested format is not available")
        self.manager.download.side_effect = [unavailable, self.writer()(URL, "", "", post_hook=lambda p: None)]
        seen = []
        result = self.run_download(
            format_id="137",
            prompter=ScriptedPrompter(fallback=True),
            on_format_fallback=lambda fmt, label: seen.append((fmt, label)),
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.format_fallback)
        self.assertEqual(result.format_id, "best")
        self.assertEqual(self.manager.download.call_args_list[1].args[1], "best")
        self.assertEqual(seen, [("best", "Best (single file)")])

    def test_audio_format_fallback_keeps_mp3_extract(self) -> None:
        unavailable = Exception("ERROR: Requested format is not available")
        self.manager.download.side_effect = [unavailable, True]
        seen = []
        result = self.run_download(
            format_id="bestaudio",
            prompter=ScriptedPrompter(fallback=True),
            on_format_fallback=lambda fmt, label: seen.append(fmt),
        )
        self.assertTrue(result.extract_audio)
        self.assertEqual(seen, ["bestaudio"])
        self.assertTrue(self.manager.download.call_args_list[1].kwargs["extract_audio"])

    def test_fallback_declined_returns_failure(self) -> None:
        self.manager.download.side_effect = Exception("ERROR: Requested format is not available")
        result = self.run_download(format_id="137", prompter=ScriptedPrompter(fallback=False))
        self.assertFalse(result.ok)
        self.assertEqual(self.manager.download.call_count, 1)
        self.assertEqual(self.errors[0][0], "Download")

    def test_no_fallback_when_already_best(self) -> None:
        self.manager.download.side_effect = Exception("ERROR: Requested format is not available")
        prompter = ScriptedPrompter(fallback=True)
        self.assertFalse(self.run_download(format_id="best", prompter=prompter).ok)
        self.assertNotIn("format_fallback", prompter.calls)

    def test_verification_failure_lists_unverified_paths(self) -> None:
        def download(url, selector, outtmpl, progress_hook=None, post_hook=None, **kwargs):
            post_hook(str(Path(self.folder) / "missing.mp4"))
            return True

        self.manager.download.side_effect = download
        result = self.run_download()
        self.assertFalse(result.ok)
        self.assertEqual(len(result.unverified), 1)
        self.assertIsNone(result.error)

    def test_audio_without_ffmpeg_skips_mp3_extract(self) -> None:
        self.manager.download.return_value = False
        result = self.run_download(format_id="bestaudio", ffmpeg_available=False)
        self.assertFalse(result.extract_audio)
        self.assertFalse(self.manager.download.call_args.kwargs["extract_audio"])

    def test_manager_called_with_raise_errors(self) -> None:
        self.manager.download.return_value = False
        self.run_download()
        self.assertTrue(self.manager.download.call_args.kwargs["raise_errors"])

    def test_name_template_is_joined_to_folder(self) -> None:
        self.manager.download.return_value = False
        self.run_download(name_template="clip.%(ext)s")
        self.assertEqual(self.manager.download.call_args.args[2], os.path.join(self.folder, "clip.%(ext)s"))


class TestResolveMergeFormat(_Logs, unittest.TestCase):
    def merge(self, answer, *, ffmpeg_available=False, format_id="bestvideo+bestaudio/best"):
        return workflow.resolve_merge_format(
            format_id,
            "Best (A+V)",
            ffmpeg_available=ffmpeg_available,
            prompter=ScriptedPrompter(ffmpeg=answer),
            log=self.log,
            labels={"best": "Best (single file)"},
        )

    def test_fallback_continue_cancel_and_passthrough(self) -> None:
        self.assertEqual(self.merge("fallback"), ("best", "Best (single file)"))
        self.assertEqual(self.merge("continue"), ("bestvideo+bestaudio/best", "Best (A+V)"))
        self.assertIsNone(self.merge("cancel"))
        self.assertEqual(self.merge("cancel", ffmpeg_available=True)[0], "bestvideo+bestaudio/best")
        self.assertEqual(self.merge("cancel", format_id="bestaudio")[0], "bestaudio")


class TestDownloadDirect(_Logs, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "Demo.mp4")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def direct(self):
        return workflow.download_direct(
            "https://cdn.example/stream.m3u8", {"Referer": URL}, self.out, log=self.log, log_error=self.log_error
        )

    def test_verified_output(self) -> None:
        with mock.patch("doggybag.app.workflow.download_with_ffmpeg", side_effect=lambda u, o, h, **k: Path(o).write_bytes(b"x")):
            result = self.direct()
        self.assertTrue(result.ok)
        self.assertEqual(result.output.path, Path(self.out))

    def test_ffmpeg_error_logs_ffmpeg_stage(self) -> None:
        with mock.patch("doggybag.app.workflow.download_with_ffmpeg", side_effect=RuntimeError("ffmpeg failed")):
            result = self.direct()
        self.assertFalse(result.ok)
        self.assertEqual(self.errors[0][0], "ffmpeg")

    def test_missing_output_fails(self) -> None:
        with mock.patch("doggybag.app.workflow.download_with_ffmpeg", return_value=None):
            result = self.direct()
        self.assertFalse(result.ok)
        self.assertIsNone(result.error)

    def test_direct_output_path_uses_name_and_extension(self) -> None:
        path = workflow.direct_output_path(self.tmp.name, "A: talk", audio_only=True)
        self.assertEqual(Path(path).name, "A_ talk.mp3")
        path = workflow.direct_output_path(self.tmp.name, "A", audio_only=False, name="clip")
        self.assertEqual(Path(path).name, "clip.mp4")


class TestProviderTranscript(_Logs, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = mock.Mock()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def transcript(self, prompter=None):
        return workflow.download_provider_transcript(
            self.manager,
            URL,
            self.tmp.name,
            prompter=prompter or ScriptedPrompter(),
            log=self.log,
            log_error=self.log_error,
        )

    def test_saved_with_scanned_paths(self) -> None:
        def write(url, outtmpl, selected_track=None):
            (Path(self.tmp.name) / "Talk.transcript.en.vtt").write_text("WEBVTT")
            (Path(self.tmp.name) / "other.txt").write_text("x")
            return SimpleNamespace(source="subtitles", languages=["en"])

        self.manager.download_transcript.side_effect = write
        outcome = self.transcript()
        self.assertEqual(outcome.status, "saved")
        self.assertEqual([Path(p).name for p in outcome.paths], ["Talk.transcript.en.vtt"])

    def test_unavailable(self) -> None:
        self.manager.download_transcript.side_effect = NoTranscriptAvailableError("none")
        self.assertEqual(self.transcript().status, "unavailable")

    def test_cookie_retry_once(self) -> None:
        self.manager.download_transcript.side_effect = [
            CookiesRequiredError(),
            SimpleNamespace(source="subtitles", languages=["en"]),
        ]
        self.assertTrue(self.transcript(ScriptedPrompter(cookies=True)).ok)
        self.assertEqual(self.manager.download_transcript.call_count, 2)

    def test_generic_failure(self) -> None:
        self.manager.download_transcript.side_effect = RuntimeError("boom")
        outcome = self.transcript()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(self.errors[0][0], "Transcript")


class TestEntryHelpers(_Logs, unittest.TestCase):
    def test_single_entry_url_fallback_only_when_given(self) -> None:
        self.assertEqual(workflow.entry_url({"title": "x"}, fallback_url=URL), URL)
        self.assertIsNone(workflow.entry_url({"title": "x"}, fallback_url=None))
        self.assertEqual(workflow.entry_url({"webpage_url": URL}, fallback_url="https://other.example"), URL)

    def test_collection_title(self) -> None:
        self.assertIsNone(workflow.collection_title([{"playlist_title": "P"}]))
        self.assertEqual(workflow.collection_title([{"playlist_title": "P"}, {}]), "P")
        self.assertEqual(workflow.collection_title([{"channel": "C"}, {"channel": "C"}]), "C")

    def test_sanitize_entries_drops_non_dicts(self) -> None:
        self.assertEqual(workflow.sanitize_entries([{"a": 1}, None, "x"], log=self.log), [{"a": 1}])
        self.assertIn("Dropped 2 unusable entries.", self.lines)


class TestControllerPrompter(unittest.TestCase):
    def test_each_prompt_maps_to_existing_controller_dialog(self) -> None:
        app = object.__new__(AppController)
        app.main_thread_id = threading.get_ident()
        app._prepare_cookie_retry = mock.Mock(return_value=True)
        app.confirm_format_fallback = mock.Mock(return_value=False)
        app.confirm_ffmpeg_fallback = mock.Mock(return_value="fallback")
        app.ui_prompt_for_credentials = mock.Mock(return_value=("u", "p"))
        app.ui_confirm = mock.Mock(return_value=True)
        prompter = _ControllerPrompter(app)

        self.assertTrue(prompter.cookie_retry())
        self.assertFalse(prompter.format_fallback(extract_audio=True))
        app.confirm_format_fallback.assert_called_once_with(extract_audio=True)
        self.assertEqual(prompter.ffmpeg_missing("Best"), "fallback")
        self.assertEqual(prompter.credentials(URL, browser_automation=False), ("u", "p"))
        app.ui_prompt_for_credentials.assert_called_once_with(URL, browser_automation=False)
        self.assertTrue(prompter.allow_browser_automation())
        self.assertEqual(app.ui_confirm.call_args.args[0], "Fallback")


if __name__ == "__main__":
    unittest.main()
