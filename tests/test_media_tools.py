"""Section time parsing, clip naming, ffmpeg cut commands and ffprobe facts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from doggybag.core import helpers
from doggybag.core.media_tools import (
    cut_section,
    parse_ffprobe_json,
    parse_time_range,
    parse_timestamp,
    probe_media_file,
    section_extension,
    section_stem,
)


class TestTimeRanges(unittest.TestCase):
    def test_parse_time_range_forms_and_errors(self) -> None:
        self.assertEqual(parse_timestamp("90"), 90.0)
        self.assertEqual(parse_timestamp("1:23"), 83.0)
        self.assertEqual(parse_timestamp("1:02:03.5"), 3723.5)
        self.assertEqual(parse_time_range("1:23-2:45"), (83.0, 165.0))
        self.assertEqual(parse_time_range("90-"), (90.0, None))
        self.assertEqual(parse_time_range("-0:30"), (0.0, 30.0))
        for bad in ("2:45-1:23", "abc", "1:75", "1:60:00", "10", "1-1", "a-b"):
            with self.assertRaises(ValueError, msg=bad):
                parse_time_range(bad)

    def test_section_stem_naming(self) -> None:
        self.assertEqual(section_stem("Talk", 83, 165), "Talk.clip-00.01.23-00.02.45")
        self.assertEqual(section_stem("Talk", 1.25, None), "Talk.clip-00.00.01.250-end")
        self.assertEqual(section_extension("a.webm", mode="exact", audio_only=False), "mp4")
        self.assertEqual(section_extension("a.webm", mode="copy", audio_only=False), "webm")
        self.assertEqual(section_extension("a.m4a", mode="exact", audio_only=True), "mp3")


class TestCutAndProbe(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "clip.mp4"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fake_run(self, seen):
        def run(cmd, **_kwargs):
            seen.append(cmd)
            Path(cmd[-1]).write_bytes(b"clip")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        return run

    def test_cut_commands_exact_vs_copy(self) -> None:
        seen = []
        cut_section("src.webm", self.out, 10, 25, mode="exact", run=self.fake_run(seen))
        exact = seen[-1]
        self.assertIn("-n", exact)
        self.assertEqual(exact[exact.index("-ss") + 1], "10.000")
        self.assertEqual(exact[exact.index("-t") + 1], "15.000")
        self.assertIn("libx264", exact)

        self.out.unlink()
        cut_section("src.webm", self.out, 10, None, mode="copy", run=self.fake_run(seen))
        copy = seen[-1]
        self.assertNotIn("-t", copy)
        self.assertEqual(copy[copy.index("-c") + 1], "copy")

        audio_out = Path(self.tmp.name) / "clip.mp3"
        cut_section("src.m4a", audio_out, 0, 5, audio_only=True, run=self.fake_run(seen))
        self.assertIn("libmp3lame", seen[-1])

    def test_cut_failure_raises_with_ffmpeg_tail(self) -> None:
        def run(cmd, **_kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="line\nInvalid data found")

        with self.assertRaises(RuntimeError) as ctx:
            cut_section("src.webm", self.out, 0, 5, run=run)
        self.assertIn("Invalid data found", str(ctx.exception))

    def test_probe_media_file_parses_ffprobe_json_and_fps_fraction(self) -> None:
        payload = {
            "format": {"duration": "15.000000"},
            "streams": [
                {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                 "avg_frame_rate": "30000/1001"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
        facts = parse_ffprobe_json(json.dumps(payload))
        self.assertEqual((facts.width, facts.height, facts.vcodec, facts.acodec), (1920, 1080, "h264", "aac"))
        self.assertEqual(facts.fps, 29.97)
        self.assertEqual(facts.duration, 15.0)

        def run(cmd, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

        self.assertEqual(probe_media_file("x.mp4", run=run).height, 1080)
        self.assertIsNone(probe_media_file("x.mp4", run=lambda *a, **k: SimpleNamespace(returncode=1, stdout="")))


class TestFfmpegInterrupt(unittest.TestCase):
    def test_download_with_ffmpeg_kills_process_on_interrupt(self) -> None:
        def lines():
            yield "out_time_ms=1000\n"
            raise KeyboardInterrupt

        proc = mock.MagicMock()
        proc.stdout = lines()
        with mock.patch.object(helpers.subprocess, "Popen", return_value=proc), \
                mock.patch.object(helpers, "probe_media_duration", return_value=None):
            with self.assertRaises(KeyboardInterrupt):
                helpers.download_with_ffmpeg("https://cdn.example/a.m3u8", "out.mp4")
        proc.kill.assert_called_once()
        proc.wait.assert_called()


if __name__ == "__main__":
    unittest.main()
