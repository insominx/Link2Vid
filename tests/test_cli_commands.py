"""CLI commands end to end through run_cli, with faked network and tools."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from doggybag.cli.main import run_cli
from doggybag.cli.session import Session
from doggybag.core.audio_source import AudioSourceResolver
from doggybag.core.downloader import DownloadManager, TranscriptDownloadResult
from doggybag.core.errors import CookiesRequiredError
from doggybag.core.fetcher import FetchResults, NeedsCookies, NeedsLogin
from doggybag.core.whisper_transcription import TranscriptionResult
from tests.fixtures.hosts import VIDEO_HOST_A

URL = f"https://{VIDEO_HOST_A}/watch/1"
BEST = "bestvideo+bestaudio/best"


class DummyLogger:
    def debug(self, _msg):
        return None

    def warning(self, _msg):
        return None

    def error(self, _msg):
        return None


class ScriptedFetcher:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def fetch(self, url, username=None, password=None):
        self.calls.append((url, username, password))
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


class CliHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out_dir = self.tmp.name
        self.manager = DownloadManager(ydl_logger=DummyLogger())
        self.manager.download = mock.Mock(side_effect=self.write_media())
        self.manager.download_transcript = mock.Mock()
        self.fetcher = ScriptedFetcher(FetchResults([{"title": "Talk", "webpage_url": URL}]))
        self.ffmpeg = "ffmpeg"
        self.whisper = None
        self.resolver = None
        self.session = None

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_media(self, name="Talk.mp4"):
        def download(url, fmt, outtmpl, progress_hook=None, post_hook=None, extract_audio=False, raise_errors=False):
            path = Path(os.path.dirname(outtmpl)) / name
            path.write_bytes(b"media")
            if progress_hook:
                progress_hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 5})
            post_hook(str(path))
            return True

        return download

    def run_cli(self, *argv, out=True):
        stdout, stderr = io.StringIO(), io.StringIO()

        def factory(options, reporter):
            session = Session(
                options,
                reporter,
                manager=self.manager,
                fetcher=self.fetcher,
                dev_defaults={},
                settings={},
                whisper_service=self.whisper,
                audio_resolver=self.resolver,
            )
            session.ffmpeg_path = self.ffmpeg
            session.ffprobe_path = None
            self.session = session
            return session

        args = list(argv) + (["--out", self.out_dir] if out else [])
        code = run_cli(args, stdout=stdout, stderr=stderr, session_factory=factory)
        self.stderr = stderr.getvalue()
        self.stdout = stdout.getvalue()
        return code, json.loads(self.stdout)


class TestDownload(CliHarness):
    def test_download_happy_path_envelope(self) -> None:
        code, env = self.run_cli("download", URL)
        self.assertEqual(code, 0)
        self.assertEqual(env["status"], "ok")
        item = env["items"][0]
        self.assertEqual((item["status"], item["route"], item["index"]), ("ok", "yt-dlp", 1))
        self.assertEqual(item["format"]["requested"], BEST)
        self.assertFalse(item["format"]["substituted"])
        self.assertEqual(item["files"][0]["kind"], "media")
        self.assertTrue(Path(item["files"][0]["path"]).is_file())
        self.assertEqual(env["fetches"][0]["source"], "yt-dlp")
        self.assertTrue(self.manager.download.call_args.kwargs["raise_errors"])

    def test_multi_entry_without_items_needs_action(self) -> None:
        self.fetcher = ScriptedFetcher(FetchResults([{"title": "A", "webpage_url": URL}, {"title": "B", "webpage_url": URL + "2"}]))
        code, env = self.run_cli("download", URL)
        self.assertEqual(code, 4)
        self.assertEqual(env["items"][0]["error"]["code"], "items_required")
        self.assertEqual([entry["title"] for entry in env["fetches"][0]["listing"]], ["A", "B"])
        self.manager.download.assert_not_called()

    def test_partial_failure_exit_3(self) -> None:
        self.fetcher = ScriptedFetcher(FetchResults([{"title": "A", "webpage_url": URL}, {"title": "B", "webpage_url": URL + "2"}]))
        ok = self.write_media("A.mp4")

        def download(url, *args, **kwargs):
            if url.endswith("2"):
                raise Exception("HTTP Error 500: Internal Server Error")
            return ok(url, *args, **kwargs)

        self.manager.download.side_effect = download
        code, env = self.run_cli("download", URL, "--items", "all")
        self.assertEqual(code, 3)
        self.assertEqual([item["status"] for item in env["items"]], ["ok", "failed"])
        self.assertEqual(env["items"][1]["error"]["code"], "network")

    def test_cookies_required_exit_4_with_next_steps(self) -> None:
        self.fetcher = ScriptedFetcher(NeedsCookies(CookiesRequiredError(original=Exception("Sign in to confirm"))))
        code, env = self.run_cli("download", URL)
        self.assertEqual(code, 4)
        error = env["items"][0]["error"]
        self.assertEqual(error["code"], "cookies_required")
        self.assertIn("--cookies-browser", error["next_steps"][0]["add_flags"])

    def test_format_fallback_reported_as_substitution(self) -> None:
        ok = self.write_media()
        calls = []

        def download(url, fmt, outtmpl, **kwargs):
            calls.append(fmt)
            if len(calls) == 1:
                raise Exception("ERROR: Requested format is not available")
            return ok(url, fmt, outtmpl, **kwargs)

        self.manager.download.side_effect = download
        code, env = self.run_cli("download", URL, "--format", "137")
        self.assertEqual(code, 0)
        item = env["items"][0]
        self.assertEqual(calls, ["137", BEST])
        self.assertTrue(item["format"]["substituted"])
        self.assertIn("format_fallback", [w["code"] for w in item["warnings"]])

    def test_format_fallback_fail_policy(self) -> None:
        self.manager.download.side_effect = Exception("ERROR: Requested format is not available")
        code, env = self.run_cli("download", URL, "--format", "137", "--format-fallback", "fail")
        self.assertEqual(code, 4)
        self.assertEqual(env["items"][0]["error"]["code"], "format_unavailable")
        self.assertEqual(self.manager.download.call_count, 1)

    def test_missing_ffmpeg_policies(self) -> None:
        self.ffmpeg = None
        code, env = self.run_cli("download", URL)
        self.assertEqual(code, 0)
        item = env["items"][0]
        self.assertEqual(item["format"]["effective"], "best")
        self.assertIn("ffmpeg_missing_fallback", [w["code"] for w in item["warnings"]])

        self.manager.download.reset_mock()
        code, env = self.run_cli("download", URL, "--if-no-ffmpeg", "fail")
        self.assertEqual(code, 6)
        self.assertEqual(env["items"][0]["error"]["code"], "ffmpeg_missing")
        self.manager.download.assert_not_called()

    def test_section_downloads_to_workspace_cuts_and_cleans_up(self) -> None:
        seen = {}

        def fake_cut(source, output, start, end, *, mode, audio_only):
            seen["source_parent"] = Path(source).parent.name
            Path(output).write_bytes(b"clip")

        with mock.patch("doggybag.cli.commands.cut_section", side_effect=fake_cut):
            code, env = self.run_cli("download", URL, "--section", "0:10-0:25")
        self.assertEqual(code, 0)
        self.assertTrue(seen["source_parent"].startswith(".doggybag-section-"))
        files = env["items"][0]["files"]
        self.assertEqual([f["kind"] for f in files], ["clip"])
        self.assertEqual(files[0]["section"], {"start": 10.0, "end": 25.0, "mode": "exact"})
        self.assertTrue(Path(files[0]["path"]).name.startswith("Talk.clip-00.00.10-00.00.25"))
        leftovers = [p.name for p in Path(self.out_dir).iterdir()]
        self.assertEqual([name for name in leftovers if name.startswith(".doggybag-section-")], [])
        self.assertNotIn("Talk.mp4", leftovers)

    def test_sections_need_ffmpeg_before_downloading(self) -> None:
        self.ffmpeg = None
        code, env = self.run_cli("download", URL, "--section", "1-2")
        self.assertEqual(code, 6)
        self.assertEqual(env["error"]["code"], "ffmpeg_missing")
        self.manager.download.assert_not_called()

    def test_invalid_section_is_usage_error(self) -> None:
        code, env = self.run_cli("download", URL, "--section", "2:00-1:00")
        self.assertEqual(code, 2)
        self.assertEqual(env["error"]["code"], "invalid_input")

    def test_keyboard_interrupt_exit_130_with_partial_items(self) -> None:
        self.manager.download.side_effect = KeyboardInterrupt
        code, env = self.run_cli("download", URL)
        self.assertEqual(code, 130)
        self.assertEqual(env["status"], "interrupted")
        self.assertEqual(env["items"][0]["status"], "failed")
        self.assertEqual(env["items"][0]["error"]["code"], "interrupted")

    def test_no_settings_writes_or_clipboard(self) -> None:
        boom = mock.Mock(side_effect=AssertionError("CLI must not write settings"))
        with mock.patch("doggybag.core.cookie_prefs.save_user_settings", boom):
            code, env = self.run_cli("download", URL, "--cookies-browser", "firefox")
        self.assertEqual(code, 0)
        self.assertTrue(self.manager.force_browser_cookies)
        self.assertEqual(env["items"][0]["cookies"]["source"], "flag")

    def test_default_out_dir_is_cwd(self) -> None:
        previous = os.getcwd()
        os.chdir(self.out_dir)
        try:
            code, env = self.run_cli("download", URL, out=False)
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0)
        self.assertEqual(os.path.normcase(env["out_dir"]), os.path.normcase(os.path.abspath(self.out_dir)))

    def test_password_never_echoed(self) -> None:
        self.fetcher = ScriptedFetcher(NeedsLogin(Exception("members only")), FetchResults([{"title": "T", "webpage_url": URL}]))
        code, _env = self.run_cli("download", URL, "--username", "me", "--password", "hunter2")
        self.assertEqual(code, 0)
        self.assertEqual(self.fetcher.calls[1], (URL, "me", "hunter2"))
        self.assertNotIn("hunter2", self.stdout + self.stderr)

    def test_stream_mode_events(self) -> None:
        stdout = io.StringIO()

        def factory(options, reporter):
            session = Session(options, reporter, manager=self.manager, fetcher=self.fetcher, dev_defaults={}, settings={})
            session.ffmpeg_path, session.ffprobe_path = "ffmpeg", None
            return session

        code = run_cli(["download", URL, "--stream", "--out", self.out_dir], stdout=stdout, stderr=io.StringIO(),
                       session_factory=factory)
        events = [json.loads(line)["event"] for line in stdout.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual(events[0], "fetch")
        self.assertIn("item_start", events)
        self.assertIn("progress", events)
        self.assertEqual(events[-1], "result")


class TestTranscript(CliHarness):
    def setUp(self) -> None:
        super().setUp()
        self.fetcher = ScriptedFetcher(
            FetchResults(
                [
                    {
                        "title": "Talk",
                        "webpage_url": URL,
                        "subtitles": {"en-US": [{"ext": "vtt"}]},
                        "automatic_captions": {"en": [{"ext": "vtt"}], "de": [{"ext": "vtt"}]},
                    }
                ]
            )
        )

        def write(url, outtmpl, selected_track=None):
            Path(os.path.dirname(outtmpl), f"Talk.transcript.{selected_track.language}.vtt").write_text("WEBVTT")
            return TranscriptDownloadResult(source=selected_track.source, languages=[selected_track.language])

        self.manager.download_transcript.side_effect = write

    def test_transcript_list_and_lang_selection(self) -> None:
        code, env = self.run_cli("transcript", URL, "--list")
        self.assertEqual(code, 0)
        tracks = env["items"][0]["transcript_tracks"]
        self.assertIn({"source": "subtitles", "language": "en-US", "label": mock.ANY}, tracks)
        self.manager.download_transcript.assert_not_called()

        code, env = self.run_cli("transcript", URL, "--lang", "en")
        self.assertEqual(code, 0)
        track = self.manager.download_transcript.call_args.kwargs["selected_track"]
        self.assertEqual((track.source, track.language), ("subtitles", "en-US"))
        self.assertEqual(env["items"][0]["files"][0]["kind"], "transcript")

        code, env = self.run_cli("transcript", URL, "--lang", "de", "--source", "auto")
        self.assertEqual(code, 0)
        track = self.manager.download_transcript.call_args.kwargs["selected_track"]
        self.assertEqual((track.source, track.language), ("automatic captions", "de"))

    def test_unknown_language_lists_available(self) -> None:
        code, env = self.run_cli("transcript", URL, "--lang", "xx")
        self.assertEqual(code, 5)
        error = env["items"][0]["error"]
        self.assertEqual(error["code"], "no_transcript")
        self.assertIn("en-US (subtitles)", error["available"])


class FakeWhisper:
    def __init__(self) -> None:
        self.requests = []

    def list_available_models(self):
        return ["tiny", "large-v3"]

    def transcribe(self, request, source_path, on_phase=None):
        self.requests.append((request, Path(source_path)))
        text = Path(request.output_dir) / f"{request.title}.whisper.en.txt"
        srt = Path(request.output_dir) / f"{request.title}.whisper.en.srt"
        text.write_text("hello")
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
        return TranscriptionResult(
            text_path=text, srt_path=srt, language="en", segment_count=1,
            model_name=request.model_name, effective_device="cpu", fallback_used=True,
        )


class TestTranscribe(CliHarness):
    def setUp(self) -> None:
        super().setUp()
        self.whisper = FakeWhisper()
        self.resolver = AudioSourceResolver(download_manager=self.manager)

    def test_transcribe_local_file_reuses_media_without_download(self) -> None:
        media = Path(self.out_dir) / "clip.mp4"
        media.write_bytes(b"media")
        code, env = self.run_cli("transcribe", str(media), "--model", "tiny")
        self.assertEqual(code, 0, env)
        self.manager.download.assert_not_called()
        request, source = self.whisper.requests[0]
        self.assertEqual(source, media.resolve())
        self.assertEqual(request.model_name, "tiny")
        item = env["items"][0]
        self.assertEqual([f["kind"] for f in item["files"]], ["whisper_text", "whisper_srt"])
        self.assertEqual(item["whisper"]["language"], "en")

    def test_unknown_model_is_usage_error(self) -> None:
        media = Path(self.out_dir) / "clip.mp4"
        media.write_bytes(b"media")
        code, env = self.run_cli("transcribe", str(media), "--model", "huge")
        self.assertEqual(code, 2)
        self.assertEqual(env["error"]["code"], "whisper_model_invalid")

    def test_whisper_unavailable_is_dependency_error(self) -> None:
        self.whisper = mock.Mock()
        self.whisper.list_available_models.side_effect = ImportError("No module named 'faster_whisper'")
        code, env = self.run_cli("transcribe", URL)
        self.assertEqual(code, 6)
        self.assertEqual(env["error"]["code"], "whisper_unavailable")


class TestInfo(CliHarness):
    def test_info_projects_entries_and_presets(self) -> None:
        code, env = self.run_cli("info", URL)
        self.assertEqual(code, 0)
        item = env["items"][0]
        self.assertEqual((item["title"], item["url"], item["status"]), ("Talk", URL, "ok"))
        self.assertIn("formats", item)
        self.assertIn("best", [preset["name"] for preset in env["report"]["presets"]])
        self.manager.download.assert_not_called()

    def test_info_does_not_require_items(self) -> None:
        self.fetcher = ScriptedFetcher(FetchResults([{"title": "A", "webpage_url": URL}, {"title": "B", "webpage_url": URL + "2"}]))
        code, env = self.run_cli("info", URL)
        self.assertEqual(code, 0)
        self.assertEqual(len(env["items"]), 2)
        self.assertNotIn("formats", env["items"][0])


if __name__ == "__main__":
    unittest.main()
