import unittest

from link2vid.core.diagnostics import build_diagnostics


class TestDiagnostics(unittest.TestCase):
    def test_build_diagnostics_contains_required_fields(self):
        lines = build_diagnostics(
            url="https://example.com/video",
            selected_title="Example",
            selected_format="best",
            yt_dlp_version="1.2.3",
            ffmpeg_path="C:\\ffmpeg.exe",
            js_runtime="node",
            js_runtime_used="node",
            js_runtime_path="C:\\node.exe",
            remote_components=["ejs:github"],
            cookies_mode="cookies.txt",
            cookies_browser="brave",
            last_error="Boom",
            last_error_reason="cookies/auth",
            log_history=["line1", "line2"],
        )
        output = "\n".join(lines)
        self.assertIn("Link2Vid Diagnostics", output)
        self.assertIn("URL: https://example.com/video", output)
        self.assertIn("Selected format: best", output)
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


if __name__ == "__main__":
    unittest.main()
