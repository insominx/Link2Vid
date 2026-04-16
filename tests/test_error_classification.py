import unittest

from link2vid.core.error_classification import classify_error, get_error_guidance


class TestErrorClassification(unittest.TestCase):
    def test_classification_table(self):
        cases = [
            ("HTTP Error 403: Forbidden", "cookies/auth"),
            ("No supported JavaScript runtime could be found", "js runtime"),
            ("n challenge solving failed", "js runtime"),
            ("ERROR: Requested format is not available", "format unavailable"),
            ("Only images are available for download", "format unavailable"),
            ("ffmpeg not found on PATH", "ffmpeg"),
            ("No transcript/subtitles available for this video.", "no transcript"),
            ("unable to extract uploader id", "extractor drift/SABR"),
            ("HTTP Error 429: Too Many Requests", "network/rate-limit"),
            ("sign in required after HTTP Error 429: Too Many Requests", "network/rate-limit"),
            ("The extractor specified to use impersonation for this download, but no impersonate target is available", "extractor drift/SABR"),
            ("some random error", "unknown"),
        ]
        for err_text, expected in cases:
            with self.subTest(err_text=err_text):
                reason, hint = classify_error(err_text)
                self.assertEqual(reason, expected)
                if expected != "unknown":
                    self.assertTrue(hint)

    def test_error_guidance_for_js_runtime(self):
        guidance = get_error_guidance("n challenge solving failed")
        self.assertIsNotNone(guidance)
        key, title, message = guidance
        self.assertEqual(key, "js runtime")
        self.assertIn("JavaScript runtime required", title)
        self.assertIn("deno/node/bun", message)

    def test_error_guidance_for_rate_limit(self):
        guidance = get_error_guidance("HTTP Error 429: Too Many Requests")
        self.assertIsNotNone(guidance)
        key, title, message = guidance
        self.assertEqual(key, "network/rate-limit")
        self.assertIn("Rate limited", title)
        self.assertIn("cookies.txt", message)

    def test_error_guidance_for_impersonation(self):
        guidance = get_error_guidance(
            "The extractor specified to use impersonation for this download, but no impersonate target is available"
        )
        self.assertIsNotNone(guidance)
        key, title, message = guidance
        self.assertEqual(key, "extractor drift/SABR:impersonation")
        self.assertIn("impersonation", title.lower())
        self.assertIn("update yt-dlp", message.lower())

    def test_error_guidance_absent_for_generic_unknown(self):
        self.assertIsNone(get_error_guidance("some random error"))


if __name__ == "__main__":
    unittest.main()
