import unittest

from link2vid.ui.main_window import ffmpeg_progress_display


class TestFfmpegProgressDisplay(unittest.TestCase):
    def test_ffmpeg_progress_display_clamps_elapsed_to_duration(self):
        pct, elapsed, total = ffmpeg_progress_display(
            0.99,
            elapsed=31 * 60 + 48,
            duration=8 * 60 + 17,
        )

        self.assertEqual(pct, 99)
        self.assertEqual(elapsed, "8:17")
        self.assertEqual(total, "8:17")

    def test_ffmpeg_progress_display_clamps_percent_to_100(self):
        pct, elapsed, total = ffmpeg_progress_display(
            1.2,
            elapsed=90,
            duration=100,
        )

        self.assertEqual(pct, 100)
        self.assertEqual(elapsed, "1:30")
        self.assertEqual(total, "1:40")

    def test_ffmpeg_progress_display_uses_fraction_when_elapsed_missing(self):
        pct, elapsed, total = ffmpeg_progress_display(
            0.5,
            elapsed=None,
            duration=100,
        )

        self.assertEqual(pct, 50)
        self.assertEqual(elapsed, "0:50")
        self.assertEqual(total, "1:40")


if __name__ == "__main__":
    unittest.main()
