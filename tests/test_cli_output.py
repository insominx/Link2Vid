"""Output contract: entry projection, error codes, exit priority, reporter modes."""

from __future__ import annotations

import io
import json
import math
import unittest

from doggybag.cli.commands import Run
from doggybag.cli.main import _exit_for
from doggybag.cli.output import (
    CliError,
    Reporter,
    entry_summary,
    error_code_for,
    error_info,
    next_steps_for,
    render_text,
)
from doggybag.core.errors import CookiesRequiredError, DownloadStalledError, NoTranscriptAvailableError
from doggybag.core.gdcvault import GdcVaultLoginRequired
from tests.fixtures.hosts import VIDEO_HOST_A

URL = f"https://{VIDEO_HOST_A}/watch/1"


class TestEntrySummary(unittest.TestCase):
    def test_entry_summary_drops_headers_urls_and_non_json_values(self) -> None:
        entry = {
            "id": "abc",
            "title": "Talk",
            "webpage_url": URL,
            "duration": float("nan"),
            "http_headers": {"Cookie": "secret=1"},
            "formats": [
                {
                    "format_id": "137",
                    "url": "https://cdn.example/video?sig=TOKEN",
                    "http_headers": {"Cookie": "secret=1"},
                    "height": 1080,
                    "fps": 30,
                    "tbr": float("inf"),
                }
            ],
            "chapters": [{"title": "Intro", "start_time": 0.0, "end_time": 12.5}],
        }
        summary = entry_summary(entry, index=1, fallback_url=None, include_formats=True)
        text = json.dumps(summary)
        self.assertNotIn("secret", text)
        self.assertNotIn("TOKEN", text)
        self.assertIsNone(summary["duration"])
        self.assertIsNone(summary["formats"][0]["tbr"])
        self.assertEqual(summary["formats"][0]["height"], 1080)
        self.assertEqual(summary["chapters"], [{"title": "Intro", "start": 0.0, "end": 12.5}])

    def test_direct_entries_hide_media_url(self) -> None:
        entry = {"title": "Stream", "webpage_url": "https://cdn.example/a.m3u8?token=x", "_ffmpeg_headers": {"Cookie": "c"}}
        summary = entry_summary(entry, index=1, fallback_url=URL)
        self.assertIsNone(summary["url"])
        self.assertEqual(summary["route"], "direct")
        self.assertNotIn("token", json.dumps(summary))


class TestErrorCodes(unittest.TestCase):
    def test_error_codes_prefer_exception_types_over_text(self) -> None:
        # The text alone would classify as a network error.
        self.assertEqual(
            error_code_for(CookiesRequiredError(original=Exception("connection timed out"))),
            "cookies_required",
        )
        self.assertEqual(error_code_for(GdcVaultLoginRequired("members only")), "credentials_required")
        self.assertEqual(error_code_for(NoTranscriptAvailableError("none")), "no_transcript")
        self.assertEqual(error_code_for(DownloadStalledError(rate=1.0, window=30)), "download_stalled")
        self.assertEqual(error_code_for(CliError("items_required", "pick")), "items_required")

    def test_classify_fallback_codes(self) -> None:
        self.assertEqual(error_code_for(Exception("ERROR: Requested format is not available")), "format_unavailable")
        self.assertEqual(error_code_for(Exception("HTTP Error 429: Too Many Requests")), "network")
        self.assertEqual(error_code_for(Exception("n challenge solving failed")), "js_runtime")
        self.assertEqual(error_code_for(Exception("something odd")), "download_failed")

    def test_next_steps_carry_exact_flags(self) -> None:
        steps = next_steps_for("cookies_required")
        self.assertIn("--cookies-browser", steps[0]["add_flags"])
        self.assertIn("--allow-browser", next_steps_for("browser_automation_required")[0]["add_flags"])
        info = error_info(CliError("items_required", "pick"), url=URL, count=3)
        self.assertEqual(info["next_steps"][0]["add_flags"], ["--items", "1"])
        self.assertEqual(info["url"], URL)


class TestExitCodes(unittest.TestCase):
    def run_with(self, *items) -> tuple[int, str]:
        run = Run(command="download")
        run.items = list(items)
        return _exit_for(run, False)

    @staticmethod
    def failed(code: str) -> dict:
        return {"status": "failed", "error": {"code": code}}

    def test_exit_code_priority(self) -> None:
        self.assertEqual(self.run_with({"status": "ok"}, {"status": "already_exists"}), (0, "ok"))
        self.assertEqual(self.run_with({"status": "ok"}, self.failed("network")), (3, "partial"))
        self.assertEqual(
            self.run_with(self.failed("network"), self.failed("no_transcript"), self.failed("cookies_required")),
            (4, "needs_action"),
        )
        self.assertEqual(self.run_with(self.failed("network"), self.failed("ffmpeg_missing")), (6, "failed"))
        self.assertEqual(self.run_with(self.failed("no_media_found")), (5, "failed"))
        self.assertEqual(self.run_with(self.failed("network")), (1, "failed"))
        run = Run(command="download")
        self.assertEqual(_exit_for(run, True), (130, "interrupted"))


class TestReporter(unittest.TestCase):
    def test_stream_mode_writes_only_ndjson_to_stdout(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        reporter = Reporter(stdout=out, stderr=err, mode="stream")
        reporter.log("a log line")
        reporter.event("fetch", url=URL)
        reporter.progress(1, "download", 0.5, speed=float("nan"))
        reporter.result({"command": "download", "items": []})
        lines = out.getvalue().splitlines()
        events = [json.loads(line)["event"] for line in lines]
        self.assertEqual(events, ["fetch", "progress", "result"])
        self.assertIn("a log line", err.getvalue())
        self.assertNotIn("NaN", out.getvalue())

    def test_json_mode_prints_one_document_and_logs_to_stderr(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        reporter = Reporter(stdout=out, stderr=err)
        reporter.event("fetch", url=URL)
        reporter.result({"value": math.inf})
        self.assertEqual(json.loads(out.getvalue()), {"value": None})

    def test_quiet_suppresses_stderr(self) -> None:
        err = io.StringIO()
        Reporter(stdout=io.StringIO(), stderr=err, quiet=True).log("hidden")
        self.assertEqual(err.getvalue(), "")

    def test_text_mode_summary(self) -> None:
        text = render_text(
            {
                "command": "download",
                "status": "needs_action",
                "exit_code": 4,
                "items": [
                    {
                        "index": 1,
                        "title": "Talk",
                        "status": "failed",
                        "files": [],
                        "error": {"code": "cookies_required", "message": "Sign in",
                                  "next_steps": next_steps_for("cookies_required")},
                    }
                ],
            }
        )
        self.assertIn("FAIL [1] Talk", text)
        self.assertIn("Try: --cookies-browser firefox", text)


if __name__ == "__main__":
    unittest.main()
