import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "packaging" / "link2vid.spec"
BUILD_BAT = ROOT / "build_windows.bat"

REQUIRED_HIDDENIMPORTS = {
    "yt_dlp_ejs",
    "PIL",
    "m3u8",
    "selenium",
    "requests",
}

REQUIRED_COLLECT_ALL = {
    "yt_dlp",
    "customtkinter",
}


class TestPackagingContract(unittest.TestCase):
    def test_spec_exists(self):
        self.assertTrue(SPEC_PATH.is_file(), f"missing committed spec: {SPEC_PATH}")

    def test_spec_collect_all_targets(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("collect_all(", text)
        for pkg in REQUIRED_COLLECT_ALL:
            self.assertIn(pkg, text, f"spec must collect_all package {pkg}")

    def test_spec_hiddenimports(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        for name in REQUIRED_HIDDENIMPORTS:
            self.assertRegex(
                text,
                rf"['\"]{re.escape(name)}['\"]",
                f"spec hiddenimports must include {name}",
            )

    def test_spec_onedir_collect(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("COLLECT(", text)
        self.assertIn("exclude_binaries=True", text)
        self.assertRegex(text, r'name=["\']Link2Vid["\']')

    def test_spec_entry_point(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("video_downloader.py", text)

    def test_build_script_outputs_release_folder(self):
        text = BUILD_BAT.read_text(encoding="utf-8")
        self.assertIn("--distpath release", text)
        self.assertIn("release\\Link2Vid", text)
        self.assertIn("Link2Vid.bat", text)


if __name__ == "__main__":
    unittest.main()
