import unittest
from pathlib import Path

from link2vid.core.diagnostics import (
    WHISPER_DIAGNOSTIC_MAX_LENGTH,
    WhisperDiagnosticsInput,
    build_diagnostics,
    sanitize_whisper_diagnostic_scalar,
    sanitize_whisper_error_reason,
)


def _legacy_kwargs(**overrides):
    values = {
        "url": "https://example.com/video",
        "action_kind": "transcript",
        "selected_title": "Example",
        "selected_format": "best",
        "output_path": "C:\\Users\\mlinh\\Downloads",
        "debug_log_path": "C:\\Users\\mlinh\\Downloads\\link2vid-debug.log",
        "transcript_source": "subtitles",
        "transcript_languages": ["en", "es"],
        "yt_dlp_version": "1.2.3",
        "ffmpeg_path": "C:\\ffmpeg.exe",
        "js_runtime": "node",
        "js_runtime_used": "node",
        "js_runtime_path": "C:\\node.exe",
        "remote_components": ["ejs:github"],
        "cookies_mode": "cookies.txt",
        "cookies_browser": "brave",
        "last_error": "Boom",
        "last_error_reason": "cookies/auth",
        "log_history": ["line1", "line2"],
    }
    values.update(overrides)
    return values


class TestDiagnostics(unittest.TestCase):
    def test_build_diagnostics_contains_required_fields(self):
        lines = build_diagnostics(**_legacy_kwargs())
        output = "\n".join(lines)
        self.assertIn("Link2Vid Diagnostics", output)
        self.assertIn("URL: https://example.com/video", output)
        self.assertIn("Action kind: transcript", output)
        self.assertIn("Selected format: best", output)
        self.assertIn("Output path: C:\\Users\\mlinh\\Downloads", output)
        self.assertIn("Debug log: C:\\Users\\mlinh\\Downloads\\link2vid-debug.log", output)
        self.assertIn("Transcript source: subtitles", output)
        self.assertIn("Transcript languages: en, es", output)
        self.assertIn("yt-dlp version: 1.2.3", output)
        self.assertIn("ffmpeg: C:\\ffmpeg.exe", output)
        self.assertIn("JS runtime: node", output)
        self.assertIn("yt-dlp JS runtime: node", output)
        self.assertIn("yt-dlp JS runtime path: C:\\node.exe", output)
        self.assertIn("EJS remote components: ejs:github", output)
        self.assertIn("Cookies mode: cookies.txt", output)
        self.assertIn("Cookies browser: brave", output)
        self.assertIn("Last error: Boom", output)
        self.assertIn("Last classified error: cookies/auth", output)
        self.assertTrue(lines[-1].endswith("line2"))
        self.assertNotIn("Whisper model:", output)
        self.assertEqual(lines.index("-- Recent log --"), len(lines) - 3)

    def test_legacy_order_unchanged_without_whisper(self):
        lines = build_diagnostics(**_legacy_kwargs(whisper=None))
        expected_prefix = [
            "Link2Vid Diagnostics",
            "URL: https://example.com/video",
            "Action kind: transcript",
            "Selected title: Example",
            "Selected format: best",
            "Output path: C:\\Users\\mlinh\\Downloads",
            "Debug log: C:\\Users\\mlinh\\Downloads\\link2vid-debug.log",
            "Transcript source: subtitles",
            "Transcript languages: en, es",
            "yt-dlp version: 1.2.3",
            "ffmpeg: C:\\ffmpeg.exe",
            "JS runtime: node",
            "yt-dlp JS runtime: node",
            "yt-dlp JS runtime path: C:\\node.exe",
            "EJS remote components: ejs:github",
            "Cookies mode: cookies.txt",
            "Cookies browser: brave",
            "Last error: Boom",
            "Last classified error: cookies/auth",
            "-- Recent log --",
            "line1",
            "line2",
        ]
        self.assertEqual(lines, expected_prefix)

    def test_no_whisper_omits_whisper_section(self):
        lines = build_diagnostics(**_legacy_kwargs())
        joined = "\n".join(lines)
        self.assertNotIn("Whisper model:", joined)
        self.assertNotIn("Whisper phase:", joined)
        self.assertNotIn("Whisper cache root:", joined)

    def test_preparing_no_source_fields_use_n_a(self):
        whisper = WhisperDiagnosticsInput(
            model_name="small",
            phase="preparing_audio",
            cache_root="C:\\Users\\mlinh\\AppData\\Local\\Link2Vid\\models",
            source_kind=None,
            effective_device=None,
            fallback_used=False,
            error_reason=None,
        )
        lines = build_diagnostics(**_legacy_kwargs(whisper=whisper))
        self.assertIn("Whisper model: small", lines)
        self.assertIn("Whisper phase: preparing_audio", lines)
        self.assertIn(
            "Whisper cache root: C:\\Users\\mlinh\\AppData\\Local\\Link2Vid\\models",
            lines,
        )
        self.assertIn("Whisper source kind: n/a", lines)
        self.assertIn("Whisper effective device: n/a", lines)
        self.assertIn("Whisper fallback used: no", lines)
        self.assertIn("Whisper error reason: n/a", lines)
        # Whisper block sits immediately before the log tail.
        self.assertEqual(
            lines[lines.index("Last classified error: n/a") + 1],
            "Whisper model: small",
        )
        self.assertEqual(
            lines[lines.index("Whisper error reason: n/a") + 1],
            "-- Recent log --",
        )

    def test_reused_success_fields(self):
        whisper = WhisperDiagnosticsInput(
            model_name="small",
            phase="complete",
            cache_root="C:\\models",
            source_kind="reused",
            effective_device="cuda",
            fallback_used=False,
            error_reason=None,
        )
        lines = build_diagnostics(**_legacy_kwargs(whisper=whisper))
        self.assertIn("Whisper source kind: reused", lines)
        self.assertIn("Whisper effective device: cuda", lines)
        self.assertIn("Whisper fallback used: no", lines)
        self.assertIn("Whisper error reason: n/a", lines)

    def test_temporary_success_fields(self):
        whisper = WhisperDiagnosticsInput(
            model_name="base",
            phase="complete",
            cache_root="C:\\models",
            source_kind="temporary",
            effective_device="cuda",
            fallback_used=False,
            error_reason=None,
        )
        lines = build_diagnostics(**_legacy_kwargs(whisper=whisper))
        self.assertIn("Whisper model: base", lines)
        self.assertIn("Whisper source kind: temporary", lines)
        self.assertIn("Whisper effective device: cuda", lines)

    def test_cpu_fallback_fields(self):
        whisper = WhisperDiagnosticsInput(
            model_name="small",
            phase="complete",
            cache_root="C:\\models",
            source_kind="reused",
            effective_device="cpu",
            fallback_used=True,
            error_reason=None,
        )
        lines = build_diagnostics(**_legacy_kwargs(whisper=whisper))
        self.assertIn("Whisper effective device: cpu", lines)
        self.assertIn("Whisper fallback used: yes", lines)
        self.assertIn("Whisper error reason: n/a", lines)

    def test_failed_sanitized_reason(self):
        whisper = WhisperDiagnosticsInput(
            model_name="small",
            phase="failed",
            cache_root="C:\\models",
            source_kind="temporary",
            effective_device=None,
            fallback_used=False,
            error_reason="Audio download failed; no usable audio source was produced",
        )
        lines = build_diagnostics(**_legacy_kwargs(whisper=whisper))
        self.assertIn("Whisper phase: failed", lines)
        self.assertIn(
            "Whisper error reason: Audio download failed; no usable audio source was produced",
            lines,
        )
        joined = "\n".join(lines)
        self.assertNotIn("Traceback", joined)
        self.assertNotIn("cookies.txt contents", joined)

    def test_scalar_sanitizer_collapses_controls_and_limits_length(self):
        value = "small\r\nInjected: yes\x00\t" + ("x" * 300)
        sanitized = sanitize_whisper_diagnostic_scalar(value)
        self.assertIsNotNone(sanitized)
        self.assertNotRegex(sanitized, r"[\r\n\x00\t]")
        self.assertEqual(len(sanitized), WHISPER_DIAGNOSTIC_MAX_LENGTH)
        self.assertTrue(sanitized.endswith("..."))

    def test_error_sanitizer_redacts_secrets_urls_and_private_paths(self):
        raw = (
            "Model failed cookie=SECRET password='hunter2' "
            "Authorization: Bearer TOPSECRET "
            "from https://example.com/model?token=URLSECRET&safe=value "
            "at C:\\Users\\alice\\AppData\\Local\\Temp\\model.bin "
            "and /home/alice/.cache/model "
            "\x00\r\nTraceback (most recent call last):"
        )
        sanitized = sanitize_whisper_error_reason(raw)
        self.assertIsNotNone(sanitized)
        self.assertNotIn("SECRET", sanitized)
        self.assertNotIn("hunter2", sanitized)
        self.assertNotIn("TOPSECRET", sanitized)
        self.assertNotIn("URLSECRET", sanitized)
        self.assertNotIn("alice", sanitized)
        self.assertNotIn("Traceback", sanitized)
        self.assertNotRegex(sanitized, r"[\r\n\x00]")
        self.assertIn("cookie=[REDACTED]", sanitized)
        self.assertIn("password=[REDACTED]", sanitized)
        self.assertIn("Authorization=[REDACTED]", sanitized)
        self.assertIn("[REDACTED PATH]", sanitized)
        self.assertIn("https://example.com/model?[REDACTED]", sanitized)
        self.assertLessEqual(len(sanitized), WHISPER_DIAGNOSTIC_MAX_LENGTH)

    def test_error_sanitizer_redacts_complete_cookie_headers(self):
        for header in ("Cookie", "Set-Cookie"):
            with self.subTest(header=header):
                sanitized = sanitize_whisper_error_reason(
                    f"Request denied {header}: session=SESSIONSECRET; "
                    "csrftoken=CSRFSECRET; theme=dark; HttpOnly"
                )
                self.assertEqual(
                    sanitized,
                    f"Request denied {header}: [REDACTED]",
                )
                self.assertNotIn("session", sanitized.lower())
                self.assertNotIn("csrftoken", sanitized.lower())
                self.assertNotIn("theme", sanitized.lower())
                self.assertNotIn("HttpOnly", sanitized)

    def test_error_sanitizer_redacts_embedded_credential_keys(self):
        sanitized = sanitize_whisper_error_reason(
            "failed csrftoken=CSRFSECRET access_token=ACCESSSECRET "
            "authToken=AUTHSECRET client_secret=CLIENTSECRET safe=value"
        )
        self.assertIn("csrftoken=[REDACTED]", sanitized)
        self.assertIn("access_token=[REDACTED]", sanitized)
        self.assertIn("authToken=[REDACTED]", sanitized)
        self.assertIn("client_secret=[REDACTED]", sanitized)
        self.assertIn("safe=value", sanitized)
        for secret in (
            "CSRFSECRET",
            "ACCESSSECRET",
            "AUTHSECRET",
            "CLIENTSECRET",
        ):
            self.assertNotIn(secret, sanitized)

    def test_error_sanitizer_redacts_unquoted_paths_with_spaces_to_line_end(self):
        for path, suffix in (
            (
                "C:\\Users\\alice\\AppData\\Local\\Temp\\Whisper Jobs\\secret model.bin",
                "windows suffix leak",
            ),
            (
                "\\\\server\\share\\Whisper Jobs\\secret model.bin",
                "unc suffix leak",
            ),
            (
                "/Users/alice/Whisper Jobs/secret model.bin",
                "posix suffix leak",
            ),
            (
                "/tmp/Whisper Jobs/secret model.bin",
                "temp suffix leak",
            ),
        ):
            with self.subTest(path=path):
                sanitized = sanitize_whisper_error_reason(
                    f"Model failed at {path} {suffix}"
                )
                self.assertEqual(sanitized, "Model failed at [REDACTED PATH]")
                self.assertNotIn("secret model.bin", sanitized)
                self.assertNotIn(suffix, sanitized)

    def test_error_sanitizer_replaces_stack_banner_first_lines(self):
        for banner in (
            "Traceback (most recent call last):",
            'File "C:\\Users\\alice\\worker.py", line 42, in run',
            "Stack trace:",
        ):
            with self.subTest(banner=banner):
                self.assertEqual(
                    sanitize_whisper_error_reason(banner),
                    "Whisper transcription failed; see the debug log for details",
                )

    def test_formatter_sanitizes_all_whisper_scalars_but_keeps_cache_root(self):
        whisper = WhisperDiagnosticsInput(
            model_name="small\r\nFake: model",
            phase="failed\x00\nFake: phase",
            cache_root="C:\\models\r\nFake: cache",
            source_kind="temporary\t\nFake: source",
            effective_device="cpu\x1f\nFake: device",
            fallback_used=False,
            error_reason="failed token=SECRET\nTraceback: hidden",
        )
        lines = build_diagnostics(**_legacy_kwargs(whisper=whisper))
        whisper_lines = [line for line in lines if line.startswith("Whisper ")]
        self.assertEqual(len(whisper_lines), 7)
        self.assertIn("Whisper model: small Fake: model", whisper_lines)
        self.assertIn("Whisper phase: failed Fake: phase", whisper_lines)
        self.assertIn(
            "Whisper cache root: C:\\models Fake: cache",
            whisper_lines,
        )
        self.assertIn(
            "Whisper error reason: failed token=[REDACTED]",
            whisper_lines,
        )
        self.assertNotIn("SECRET", "\n".join(lines))
        self.assertNotIn("Traceback", "\n".join(lines))
        self.assertTrue(all("\r" not in line and "\x00" not in line for line in lines))

    def test_diagnostics_module_does_not_import_ui(self):
        diagnostics_path = Path(__file__).resolve().parents[1] / "link2vid" / "core" / "diagnostics.py"
        source = diagnostics_path.read_text(encoding="utf-8")
        self.assertNotIn("link2vid.ui", source)
        self.assertNotIn("VideoCard", source)
        self.assertNotIn("VideoDownloaderApp", source)
        self.assertNotIn("main_window", source)


if __name__ == "__main__":
    unittest.main()
