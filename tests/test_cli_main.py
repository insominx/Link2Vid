"""Entry dispatch, schema and the doggybag.bat shim for the headless CLI."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import video_downloader
from doggybag.cli.main import build_parser, run_cli
from doggybag.cli.output import ERROR_CODES, EXIT_CODES

ROOT = Path(__file__).resolve().parents[1]


class TestEntryDispatch(unittest.TestCase):
    def test_entry_routes_subcommands_to_cli_before_gui_parser(self) -> None:
        with mock.patch("doggybag.cli.run_cli", return_value=4) as run_cli_mock, \
                mock.patch("doggybag.core.runtime.bootstrap_runtime") as bootstrap:
            self.assertEqual(video_downloader.main(["download", "https://x.test/v", "--audio"]), 4)
        run_cli_mock.assert_called_once_with(["download", "https://x.test/v", "--audio"])
        bootstrap.assert_called_once()

    def test_gui_flags_smoke_and_devtools_unchanged(self) -> None:
        with mock.patch.object(video_downloader, "_run_smoke", return_value=0) as smoke, \
                mock.patch("doggybag.cli.run_cli") as run_cli_mock:
            self.assertEqual(video_downloader.main(["--smoke"]), 0)
        smoke.assert_called_once()
        run_cli_mock.assert_not_called()
        with self.assertRaises(SystemExit):
            video_downloader.main(["--not-a-flag"])

    def test_cli_import_does_not_load_ui_or_controller(self) -> None:
        code = (
            "import sys; import doggybag.cli.commands, doggybag.cli.main; "
            "bad = [m for m in ('doggybag.ui', 'doggybag.ui.webview_app', 'doggybag.app.controller', 'webview') "
            "if m in sys.modules]; print(bad); raise SystemExit(1 if bad else 0)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


class TestSchema(unittest.TestCase):
    def test_schema_lists_every_command_flag_and_exit_code(self) -> None:
        out = io.StringIO()
        self.assertEqual(run_cli(["schema"], stdout=out, stderr=io.StringIO()), 0)
        envelope = json.loads(out.getvalue())
        report = envelope["report"]
        self.assertEqual(
            set(report["commands"]),
            {"info", "download", "transcript", "transcribe", "doctor", "schema"},
        )
        download_flags = {arg["name"] for arg in report["commands"]["download"]["arguments"]}
        for flag in ("--section", "--audio", "--items", "--cookies-browser", "--format-fallback", "URL"):
            self.assertIn(flag, download_flags)
        self.assertEqual(set(report["exit_codes"]), {str(code) for code in EXIT_CODES})
        self.assertEqual(set(report["error_codes"]), set(ERROR_CODES))

    def test_parser_has_no_interactive_defaults(self) -> None:
        args = build_parser().parse_args(["download", "https://x.test/v"])
        self.assertEqual(args.format_fallback, "best")
        self.assertEqual(args.if_no_ffmpeg, "fallback")
        self.assertFalse(args.allow_browser)

    def test_usage_error_is_a_json_envelope_with_exit_2(self) -> None:
        out = io.StringIO()
        self.assertEqual(run_cli(["download", "--bogus"], stdout=out, stderr=io.StringIO()), 2)
        envelope = json.loads(out.getvalue())
        self.assertEqual(envelope["error"]["code"], "invalid_input")
        self.assertEqual(envelope["command"], "download")

    def test_windowed_build_without_stdout_returns_usage(self) -> None:
        with mock.patch.object(sys, "stdout", None):
            self.assertEqual(run_cli(["schema"]), 2)


class TestShim(unittest.TestCase):
    def test_cli_shim_gates_python_to_stderr_and_forwards_args(self) -> None:
        text = (ROOT / "doggybag.bat").read_text(encoding="utf-8")
        self.assertIn('check_python.bat" "%PYTHON%" 1>&2', text)
        self.assertNotIn("cd /d", text.lower())
        self.assertNotIn("pythonw", text.lower())
        self.assertIn('video_downloader.py" %*', text)
        self.assertIn("exit /b %ERRORLEVEL%", text)


if __name__ == "__main__":
    unittest.main()
