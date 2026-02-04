import unittest

from link2vid.core.error_classification import classify_error


class TestErrorClassification(unittest.TestCase):
    def test_classification_table(self):
        cases = [
            ("HTTP Error 403: Forbidden", "cookies/auth"),
            ("No supported JavaScript runtime could be found", "js runtime"),
            ("n challenge solving failed", "js runtime"),
            ("ERROR: Requested format is not available", "format unavailable"),
            ("Only images are available for download", "format unavailable"),
            ("ffmpeg not found on PATH", "ffmpeg"),
            ("unable to extract uploader id", "extractor drift/SABR"),
            ("HTTP Error 429: Too Many Requests", "network/rate-limit"),
            ("some random error", "unknown"),
        ]
        for err_text, expected in cases:
            with self.subTest(err_text=err_text):
                reason, hint = classify_error(err_text)
                self.assertEqual(reason, expected)
                if expected != "unknown":
                    self.assertTrue(hint)


if __name__ == "__main__":
    unittest.main()
