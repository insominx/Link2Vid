import unittest
from unittest.mock import MagicMock, patch

from link2vid.core.helpers import (
    download_with_ffmpeg,
    parse_ffmpeg_progress_ms,
    parse_ffmpeg_time_seconds,
    probe_media_duration,
)


class TestFfmpegProgressParsing(unittest.TestCase):
    def test_parse_ffmpeg_time_seconds(self):
        self.assertAlmostEqual(
            parse_ffmpeg_time_seconds(
                "frame=  123 fps= 45 q=-1.0 size=    1234kB time=00:01:23.45 bitrate= 900.0kbits/s"
            ),
            83.45,
        )

    def test_parse_ffmpeg_progress_ms(self):
        self.assertAlmostEqual(parse_ffmpeg_progress_ms("out_time_ms=83450"), 83.45)


class TestDownloadWithFfmpeg(unittest.TestCase):
    @patch("link2vid.core.helpers.os.path.isfile", return_value=True)
    @patch("link2vid.core.helpers.os.path.getsize", return_value=1024)
    @patch("link2vid.core.helpers.probe_media_duration", return_value=100.0)
    @patch("link2vid.core.helpers.subprocess.Popen")
    def test_download_with_ffmpeg_uses_real_crlf_in_headers(self, popen_mock, _duration, _size, _exists):
        proc = MagicMock()
        proc.stdout = iter(["out_time_ms=50000\n", "progress=end\n"])
        proc.wait.return_value = None
        proc.returncode = 0
        popen_mock.return_value = proc

        download_with_ffmpeg(
            "https://cdn.example.com/playlist.m3u8",
            "out.mp4",
            {"Referer": "https://player.vimeo.com/", "User-Agent": "TestAgent"},
        )

        cmd = popen_mock.call_args.args[0]
        headers_index = cmd.index("-headers")
        header_block = cmd[headers_index + 1]
        self.assertIn("\r\n", header_block)
        self.assertNotIn("\\r\\n", header_block)
        self.assertIn("Referer: https://player.vimeo.com/", header_block)
        self.assertIn("-progress", cmd)

    @patch("link2vid.core.helpers.os.path.isfile", return_value=True)
    @patch("link2vid.core.helpers.os.path.getsize", return_value=1024)
    @patch("link2vid.core.helpers.probe_media_duration", return_value=100.0)
    @patch("link2vid.core.helpers.subprocess.Popen")
    def test_download_with_ffmpeg_reports_progress(self, popen_mock, _duration, _size, _exists):
        proc = MagicMock()
        proc.stdout = iter(["out_time_ms=50000\n", "progress=end\n"])
        proc.wait.return_value = None
        proc.returncode = 0
        popen_mock.return_value = proc
        seen: list[float] = []

        download_with_ffmpeg(
            "https://cdn.example.com/playlist.m3u8",
            "out.mp4",
            progress_hook=lambda fraction, _elapsed, _duration: seen.append(fraction),
        )

        self.assertTrue(any(value >= 0.49 for value in seen))
        self.assertEqual(seen[-1], 1.0)

    @patch("link2vid.core.helpers.os.path.isfile", return_value=False)
    @patch("link2vid.core.helpers.probe_media_duration", return_value=None)
    @patch("link2vid.core.helpers.subprocess.Popen")
    def test_download_with_ffmpeg_raises_on_nonzero_exit(self, popen_mock, _duration, _exists):
        proc = MagicMock()
        proc.stdout = iter(["HTTP error 403 Forbidden\n"])
        proc.wait.return_value = None
        proc.returncode = 1
        popen_mock.return_value = proc

        with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
            download_with_ffmpeg("https://cdn.example.com/playlist.m3u8", "out.mp4")

    @patch("link2vid.core.helpers.m3u8.load")
    def test_probe_media_duration_sums_hls_segments(self, load_mock):
        segment = MagicMock(duration=120.0)
        playlist = MagicMock(is_variant=False, segments=[segment, segment])
        load_mock.return_value = playlist

        duration = probe_media_duration("https://cdn.example.com/playlist.m3u8")

        self.assertEqual(duration, 240.0)


if __name__ == "__main__":
    unittest.main()
