import os
import tempfile
import unittest

from link2vid.core.helpers import sanitize_filename, unique_output_path


class TestFilenameHelpers(unittest.TestCase):
    def test_sanitize_filename_replaces_invalid_characters(self):
        self.assertEqual(
            sanitize_filename('Bonus: Molly "Visual" Trio / Part 1'),
            "Bonus_ Molly _Visual_ Trio _ Part 1",
        )

    def test_sanitize_filename_falls_back_when_empty(self):
        self.assertEqual(sanitize_filename("   "), "video")

    def test_unique_output_path_avoids_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            first = unique_output_path(folder, "Workshop Replay", "mp4")
            open(first, "wb").close()
            second = unique_output_path(folder, "Workshop Replay", "mp4")
            self.assertTrue(first.endswith("Workshop Replay.mp4"))
            self.assertTrue(second.endswith("Workshop Replay (2).mp4"))
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
