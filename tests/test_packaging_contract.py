import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "packaging" / "doggybag.spec"
BUILD_BAT = ROOT / "build_windows.bat"

REQUIRED_HIDDENIMPORTS = {
    "yt_dlp_ejs",
    "clr",
    "m3u8",
    "selenium",
    "requests",
    "ctranslate2",
    "ctranslate2._ext",
    "av",
    "onnxruntime",
    "tokenizers",
    "huggingface_hub",
    "huggingface_hub._snapshot_download",
}

REQUIRED_COLLECT_ALL = {
    "yt_dlp",
    "webview",
    "selenium",
}


class TestPackagingContract(unittest.TestCase):
    def test_spec_exists(self):
        self.assertTrue(SPEC_PATH.is_file(), f"missing committed spec: {SPEC_PATH}")

    def test_spec_collect_all_targets(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("collect_all(", text)
        loop = re.search(r"for pkg in \(([^)]*)\):\s*\n\s*pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all\(pkg\)", text)
        self.assertIsNotNone(loop, "spec must collect_all packages in a `for pkg in (...)` loop")
        collected = set(ast.literal_eval(f"({loop.group(1)})"))
        for pkg in REQUIRED_COLLECT_ALL:
            self.assertIn(pkg, collected, f"spec must collect_all package {pkg}")

    def test_spec_hiddenimports(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        for name in REQUIRED_HIDDENIMPORTS:
            self.assertRegex(
                text,
                rf"['\"]{re.escape(name)}['\"]",
                f"spec hiddenimports must include {name}",
            )

    def test_spec_collects_whisper_assets_and_native_libraries(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn('collect_data_files("faster_whisper")', text)
        self.assertIn('collect_dynamic_libs("ctranslate2")', text)
        self.assertIn('collect_submodules("faster_whisper")', text)

    def test_spec_onedir_collect(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("COLLECT(", text)
        self.assertIn("exclude_binaries=True", text)
        self.assertRegex(text, r'name=["\']Doggybag["\']')

    def test_spec_bundles_web_frontend_and_icon(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn('ROOT / "doggybag" / "ui" / "web"', text)
        self.assertIn('"doggybag/ui/web"', text)
        self.assertIn('icon=str(ROOT / "doggybag.ico")', text)

    def test_spec_entry_point(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("video_downloader.py", text)

    def test_spec_does_not_bundle_models_or_nvidia_packages(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        expected_excludes = {
            "nvidia",
            "torch",
            "transformers",
            "tensorflow",
            "scipy",
            "sklearn",
            "cv2",
        }
        tree = ast.parse(text, filename=str(SPEC_PATH))
        analysis_call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Analysis"
        )
        excludes_node = next(
            keyword.value
            for keyword in analysis_call.keywords
            if keyword.arg == "excludes"
        )
        excludes = set(ast.literal_eval(excludes_node))
        self.assertTrue(
            expected_excludes.issubset(excludes),
            f"missing optional inference exclusions: {expected_excludes - excludes}",
        )
        self.assertNotIn("download_model(", text)
        self.assertNotIn("WhisperModel(", text)

    def test_build_script_outputs_release_folder(self):
        text = BUILD_BAT.read_text(encoding="utf-8")
        self.assertIn("--distpath release", text)
        self.assertIn("release\\Doggybag", text)
        self.assertIn("Doggybag.bat", text)

    def test_build_script_stages_real_ffmpeg_binaries(self):
        text = BUILD_BAT.read_text(encoding="utf-8")
        self.assertIn("%ChocolateyInstall%\\lib\\ffmpeg\\tools\\ffmpeg\\bin\\ffmpeg.exe", text)
        self.assertIn('"%RELEASE_DIR%\\bin\\ffmpeg.exe" -version', text)
        self.assertIn('"%RELEASE_DIR%\\bin\\ffprobe.exe" -version', text)


if __name__ == "__main__":
    unittest.main()
