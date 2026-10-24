"""Argument parsing, dispatch and the result envelope for the headless CLI."""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import time
import traceback

from ..core.cookie_prefs import COOKIE_BROWSERS
from ..core.helpers import get_yt_dlp_version
from ..core.media_tools import SECTION_MODES
from . import commands
from .commands import Run
from .output import (
    ERROR_CODES,
    EXIT_CODES,
    EXIT_DEPENDENCY,
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_NEEDS_ACTION,
    EXIT_NOTHING,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    SCHEMA_VERSION,
    CliError,
    Reporter,
    error_info,
    exit_code_for,
)
from .session import CliOptions, Session

COMMANDS = ("info", "download", "transcript", "transcribe", "doctor", "schema")

_DESCRIPTION = (
    "Doggybag headless CLI for agents and scripts. stdout is one JSON result (or NDJSON with "
    "--stream); logs and progress go to stderr. Never prompts. Run `schema` for the full contract."
)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError("invalid_input", message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="doggybag", description=_DESCRIPTION)

    output = argparse.ArgumentParser(add_help=False)
    group = output.add_argument_group("output")
    mode = group.add_mutually_exclusive_group()
    mode.add_argument("--text", action="store_true", help="human summary on stdout instead of JSON")
    mode.add_argument("--stream", action="store_true", help="NDJSON events on stdout; the last line is the result")
    group.add_argument("--quiet", action="store_true", help="no logs or progress on stderr")
    group.add_argument("--log-file", metavar="PATH", help="also append timestamped logs to this file")

    fetch = argparse.ArgumentParser(add_help=False)
    group = fetch.add_argument_group("fetch")
    group.add_argument("--items", metavar="SPEC", help="entries to use when a URL has several: all, 3, 1,4,6-9")
    group.add_argument("--flat", action="store_true", help="list playlists without resolving every entry (fast)")
    group.add_argument("--out", metavar="DIR", help="output folder (default: current directory; created if missing)")
    group.add_argument("--cookies-browser", choices=COOKIE_BROWSERS, help="use this browser's cookies from the first attempt")
    group.add_argument("--cookies-file", metavar="PATH", help="Netscape cookies.txt to send")
    group.add_argument("--username", help="site username (prefer env DOGGYBAG_USERNAME)")
    group.add_argument("--password", help="site password (prefer env DOGGYBAG_PASSWORD)")
    group.add_argument("--allow-browser", action="store_true", help="allow the Selenium fallback (opens a visible Chrome window)")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    info = sub.add_parser(
        "info", parents=[output, fetch], help="list entries, formats, chapters and caption tracks",
        description="Resolve URLs and describe each entry without downloading.",
    )
    info.add_argument("urls", nargs="+", metavar="URL")
    info.add_argument("--formats", action="store_true", help="include format tables for multi-entry results")

    download = sub.add_parser(
        "download", parents=[output, fetch], help="download video, audio or clips",
        description="Download each URL (or the selected --items) at the best source quality by default.",
    )
    download.add_argument("urls", nargs="+", metavar="URL")
    kind = download.add_mutually_exclusive_group()
    kind.add_argument("--format", metavar="ID", help="preset (best, video-only, audio) or any yt-dlp format selector")
    kind.add_argument("--audio", action="store_true", help="audio only, converted to MP3")
    kind.add_argument("--video-only", action="store_true", help="best video stream without audio")
    download.add_argument("--section", action="append", metavar="START-END",
                          help="cut a clip, e.g. 1:23-2:45, 90-, -0:30 (repeatable); downloads the full source first")
    download.add_argument("--section-mode", choices=SECTION_MODES, default="exact",
                          help="exact re-encodes for frame-accurate cuts; copy is lossless but snaps to keyframes")
    download.add_argument("--keep-full", action="store_true", help="keep the full source next to the clips")
    download.add_argument("--name", metavar="STEM", help="output file name without extension (one item only)")
    download.add_argument("--format-fallback", choices=("best", "fail"), default="best",
                          help="when the format is unavailable: retry with best (default) or fail")
    download.add_argument("--if-no-ffmpeg", choices=("fallback", "continue", "fail"), default="fallback",
                          help="when a merge format needs ffmpeg and it is missing")

    transcript = sub.add_parser(
        "transcript", parents=[output, fetch], help="download the site's subtitles or captions",
        description="Save the site's own subtitles/captions. Prefers subtitles, then English.",
    )
    transcript.add_argument("urls", nargs="+", metavar="URL")
    transcript.add_argument("--list", action="store_true", help="list available tracks instead of downloading")
    transcript.add_argument("--lang", metavar="CODE", help="language code, e.g. en or de")
    transcript.add_argument("--source", choices=("subtitles", "auto"), help="subtitles, or automatic captions")

    transcribe = sub.add_parser(
        "transcribe", parents=[output, fetch], help="transcribe a URL or local file with Whisper",
        description="Transcribe locally with Faster Whisper; writes .txt and .srt. Local files are used in place.",
    )
    transcribe.add_argument("inputs", nargs="+", metavar="URL_OR_FILE")
    transcribe.add_argument("--model", metavar="NAME", help="Whisper model alias (default large-v3; tiny is fastest)")

    sub.add_parser(
        "doctor", parents=[output], help="check ffmpeg, JS runtime, Whisper and settings",
        description="Report the environment offline: tools, versions, Whisper models, cookie settings.",
    )
    sub.add_parser(
        "schema", parents=[output], help="print the JSON contract: commands, flags, exit and error codes",
        description="Machine-readable description of every command, flag, exit code and error code.",
    )
    return parser


def build_schema(parser: argparse.ArgumentParser) -> dict:
    commands_schema = {}
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        helps = {choice.dest: choice.help for choice in action._choices_actions}
        for name, sub in action.choices.items():
            arguments = []
            for arg in sub._actions:
                if isinstance(arg, argparse._HelpAction):
                    continue
                kind = "flag" if arg.nargs == 0 else "positional" if not arg.option_strings else "option"
                default = arg.default if isinstance(arg.default, (str, int, float, bool)) else None
                arguments.append(
                    {
                        "name": arg.option_strings[0] if arg.option_strings else arg.metavar or arg.dest,
                        "kind": kind,
                        "repeatable": isinstance(arg, argparse._AppendAction),
                        "nargs": arg.nargs if isinstance(arg.nargs, str) else None,
                        "choices": list(arg.choices) if arg.choices else None,
                        "default": default,
                        "help": arg.help,
                    }
                )
            commands_schema[name] = {
                "summary": helps.get(name),
                "description": sub.description,
                "arguments": arguments,
            }
    return {
        "invocation": "doggybag.bat <command> ... | python video_downloader.py <command> ...",
        "commands": commands_schema,
        "exit_codes": {str(code): meaning for code, meaning in EXIT_CODES.items()},
        "error_codes": {code: {"exit": exit_code, "meaning": meaning} for code, (exit_code, meaning) in ERROR_CODES.items()},
        "result": {
            "envelope": ["schema", "command", "status", "ok", "exit_code", "out_dir", "inputs", "fetches",
                         "items", "warnings", "error", "report", "tools", "elapsed_seconds"],
            "item_status": ["ok", "already_exists", "failed", "skipped"],
            "envelope_status": ["ok", "partial", "failed", "needs_action", "interrupted", "degraded"],
            "file_kinds": ["media", "clip", "transcript", "whisper_text", "whisper_srt"],
            "error": ["code", "message", "type", "reason", "hint", "url", "stage", "next_steps"],
            "stream_events": ["fetch", "item_start", "progress", "phase", "item_done", "result"],
        },
    }


def _exit_for(run: Run, interrupted: bool) -> tuple[int, str]:
    if interrupted:
        return EXIT_INTERRUPTED, "interrupted"
    if run.error is not None:
        code = exit_code_for(run.error.get("code"))
        return code, "needs_action" if code == EXIT_NEEDS_ACTION else "failed"
    if run.command == "doctor":
        return EXIT_OK, "degraded" if run.warnings else "ok"
    successes = [item for item in run.items if item.get("status") in ("ok", "already_exists")]
    failures = [item for item in run.items if item.get("status") not in ("ok", "already_exists")]
    if not failures:
        return EXIT_OK, "ok"
    if successes:
        return EXIT_PARTIAL, "partial"
    codes = {exit_code_for((item.get("error") or {}).get("code") or "download_failed") for item in failures}
    for code in (EXIT_NEEDS_ACTION, EXIT_USAGE, EXIT_DEPENDENCY, EXIT_NOTHING, EXIT_FAILED):
        if code in codes:
            return code, "needs_action" if code == EXIT_NEEDS_ACTION else "failed"
    return EXIT_FAILED, "failed"


def _tools(session: Session | None) -> dict | None:
    if session is None:
        return None
    try:
        js_runtime = session.manager._select_js_runtime()[0]
    except Exception:
        js_runtime = None
    return {"yt_dlp": get_yt_dlp_version(), "ffmpeg": session.ffmpeg_path, "js_runtime": js_runtime}


def _envelope(run: Run, exit_code: int, status: str, started: float, session: Session | None) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "command": run.command,
        "status": status,
        "ok": exit_code == EXIT_OK,
        "exit_code": exit_code,
        "out_dir": run.out_dir,
        "inputs": run.inputs,
        "fetches": run.fetches,
        "items": run.items,
        "warnings": run.warnings,
        "error": run.error,
        "report": run.report,
        "tools": _tools(session),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def _prepare_out_dir(path: str | None) -> str:
    out_dir = os.path.abspath(path or os.getcwd())
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise CliError("invalid_input", f"Cannot create --out {out_dir}: {exc}") from None
    if not os.access(out_dir, os.W_OK):
        raise CliError("invalid_input", f"--out {out_dir} is not writable.")
    return out_dir


def _options(args, out_dir: str) -> CliOptions:
    cookies_file = getattr(args, "cookies_file", None)
    if cookies_file and not os.path.isfile(cookies_file):
        raise CliError("invalid_input", f"--cookies-file {cookies_file} does not exist.")
    return CliOptions(
        out_dir=out_dir,
        cookies_browser=getattr(args, "cookies_browser", None),
        cookies_file=os.path.abspath(cookies_file) if cookies_file else None,
        username=getattr(args, "username", None),
        password=getattr(args, "password", None),
        allow_browser=bool(getattr(args, "allow_browser", False)),
        flat=bool(getattr(args, "flat", False)),
        format_fallback=getattr(args, "format_fallback", "best"),
        if_no_ffmpeg=getattr(args, "if_no_ffmpeg", "fallback"),
    )


def _use_utf8(stream) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


_HANDLERS = {
    "info": commands.cmd_info,
    "download": commands.cmd_download,
    "transcript": commands.cmd_transcript,
    "transcribe": commands.cmd_transcribe,
    "doctor": commands.cmd_doctor,
}


def run_cli(argv: list[str], *, stdout=None, stderr=None, session_factory=None) -> int:
    """Run one CLI command; returns the process exit code."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    if out is None:
        # Windowed (pythonw / frozen GUI) builds have no stdout to report to.
        return EXIT_USAGE
    if stdout is None:
        _use_utf8(out)
    if stderr is None and err is not None:
        _use_utf8(err)

    started = time.monotonic()
    parser = build_parser()
    argv = list(argv)
    try:
        args = parser.parse_args(argv)
    except CliError as exc:
        mode = "text" if "--text" in argv else "stream" if "--stream" in argv else "json"
        reporter = Reporter(stdout=out, stderr=err, mode=mode)
        reporter.log(f"doggybag: {exc.message}")
        run = Run(command=argv[0] if argv and argv[0] in COMMANDS else None)
        run.error = error_info(exc)
        reporter.result(_envelope(run, EXIT_USAGE, "failed", started, None))
        return EXIT_USAGE

    mode = "stream" if args.stream else "text" if args.text else "json"
    reporter = Reporter(stdout=out, stderr=err, mode=mode, quiet=args.quiet, log_file=args.log_file)
    run = Run(command=args.command, inputs=list(getattr(args, "urls", None) or getattr(args, "inputs", None) or []))
    session = None
    interrupted = False
    sink = err if err is not None else io.StringIO()
    try:
        # Anything that prints (third-party code) must not corrupt the JSON on stdout.
        with contextlib.redirect_stdout(sink):
            if args.command == "schema":
                run.report = build_schema(parser)
            else:
                run.out_dir = _prepare_out_dir(getattr(args, "out", None)) if args.command != "doctor" else None
                options = _options(args, run.out_dir or os.getcwd())
                session = (session_factory or Session)(options, reporter)
                _HANDLERS[args.command](session, run, args)
    except KeyboardInterrupt:
        interrupted = True
        reporter.log("Interrupted.")
        for item in run.items:
            if item.get("status") == "running":
                item["status"] = "failed"
                item["error"] = error_info(KeyboardInterrupt())
                item["error"]["message"] = "Interrupted."
    except CliError as exc:
        run.error = error_info(exc)
    except Exception as exc:
        reporter.log("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip())
        run.error = error_info(exc)

    exit_code, status = _exit_for(run, interrupted)
    reporter.result(_envelope(run, exit_code, status, started, session))
    return exit_code


__all__ = ["COMMANDS", "build_parser", "build_schema", "run_cli"]
