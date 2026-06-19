import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from link2vid.core import runtime


class TestRuntime(unittest.TestCase):
    def setUp(self):
        runtime._bootstrap_done = False

    def test_app_dir_dev_uses_entry_script_parent(self):
        entry = Path(__file__).resolve().parents[1] / "video_downloader.py"
        with mock.patch.object(sys, "argv", [str(entry)]):
            with mock.patch.object(runtime, "is_frozen", return_value=False):
                self.assertEqual(runtime.app_dir(), entry.parent)

    def test_app_dir_frozen_uses_exe_parent_not_meipass(self):
        exe = Path("C:/Apps/Link2Vid/Link2Vid.exe")
        with mock.patch.object(runtime, "is_frozen", return_value=True):
            with mock.patch.object(sys, "executable", str(exe)):
                with mock.patch.object(sys, "_MEIPASS", "C:/Apps/Link2Vid/_internal", create=True):
                    self.assertEqual(runtime.app_dir(), exe.parent)

    def test_resolve_developer_json_dev_prefers_cwd(self):
        with mock.patch.object(runtime, "is_frozen", return_value=False):
            with tempfile_dev_json(cwd=True, app_dir=False, appdata=False) as paths:
                with mock.patch.object(Path, "cwd", return_value=paths["cwd"]):
                    with mock.patch.object(runtime, "app_dir", return_value=paths["app_dir"]):
                        with mock.patch.object(runtime, "_appdata_developer_json", return_value=paths["appdata"]):
                            resolved = runtime.resolve_developer_json()
                            self.assertEqual(resolved, paths["cwd"] / "developer.json")

    def test_resolve_developer_json_dev_falls_back_to_app_dir(self):
        with mock.patch.object(runtime, "is_frozen", return_value=False):
            with tempfile_dev_json(cwd=False, app_dir=True, appdata=False) as paths:
                with mock.patch.object(Path, "cwd", return_value=paths["cwd"]):
                    with mock.patch.object(runtime, "app_dir", return_value=paths["app_dir"]):
                        with mock.patch.object(runtime, "_appdata_developer_json", return_value=paths["appdata"]):
                            resolved = runtime.resolve_developer_json()
                            self.assertEqual(resolved, paths["app_dir"] / "developer.json")

    def test_resolve_developer_json_frozen_prefers_exe_dir(self):
        with mock.patch.object(runtime, "is_frozen", return_value=True):
            with tempfile_dev_json(cwd=True, app_dir=True, appdata=False) as paths:
                with mock.patch.object(runtime, "app_dir", return_value=paths["app_dir"]):
                    with mock.patch.object(runtime, "_appdata_developer_json", return_value=paths["appdata"]):
                        resolved = runtime.resolve_developer_json()
                        self.assertEqual(resolved, paths["app_dir"] / "developer.json")

    def test_resolve_developer_json_missing_returns_none(self):
        with mock.patch.object(runtime, "is_frozen", return_value=False):
            with tempfile_dev_json(cwd=False, app_dir=False, appdata=False) as paths:
                with mock.patch.object(Path, "cwd", return_value=paths["cwd"]):
                    with mock.patch.object(runtime, "app_dir", return_value=paths["app_dir"]):
                        with mock.patch.object(runtime, "_appdata_developer_json", return_value=paths["appdata"]):
                            self.assertIsNone(runtime.resolve_developer_json())

    def test_bootstrap_prepends_bin_to_path(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            stub = bin_dir / "ffmpeg.cmd"
            stub.write_text("@echo off\n", encoding="utf-8")

            old_path = os.environ.get("PATH", "")
            try:
                with mock.patch.object(runtime, "app_dir", return_value=root):
                    runtime.bootstrap_runtime()
                    self.assertTrue(str(bin_dir) in os.environ["PATH"].split(os.pathsep))
                    runtime.bootstrap_runtime()
                    self.assertEqual(
                        os.environ["PATH"].count(str(bin_dir)),
                        1,
                        "bootstrap_runtime should be idempotent",
                    )
            finally:
                os.environ["PATH"] = old_path
                runtime._bootstrap_done = False

    def test_startup_summary_includes_developer_json_path(self):
        with mock.patch.object(runtime, "is_frozen", return_value=True):
            with tempfile_dev_json(cwd=False, app_dir=True, appdata=False) as paths:
                with mock.patch.object(runtime, "app_dir", return_value=paths["app_dir"]):
                    with mock.patch.object(runtime, "_appdata_developer_json", return_value=paths["appdata"]):
                        summary = runtime.startup_summary()
                        self.assertIn("frozen=True", summary)
                        self.assertIn("developer.json=", summary)
                        self.assertIn(str(paths["app_dir"] / "developer.json"), summary)


class tempfile_dev_json:
    def __init__(self, cwd=True, app_dir=True, appdata=True):
        self.create_cwd = cwd
        self.create_app_dir = app_dir
        self.create_appdata = appdata

    def __enter__(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        paths = {
            "cwd": root / "cwd",
            "app_dir": root / "app_dir",
            "appdata": root / "appdata" / "Link2Vid" / "developer.json",
        }
        paths["cwd"].mkdir(parents=True, exist_ok=True)
        paths["app_dir"].mkdir(parents=True, exist_ok=True)
        paths["appdata"].parent.mkdir(parents=True, exist_ok=True)

        payload = {"use_defaults": True, "default_url": "https://example.com/watch?v=1"}
        if self.create_cwd:
            (paths["cwd"] / "developer.json").write_text(json.dumps(payload), encoding="utf-8")
        if self.create_app_dir:
            (paths["app_dir"] / "developer.json").write_text(json.dumps(payload), encoding="utf-8")
        if self.create_appdata:
            paths["appdata"].write_text(json.dumps(payload), encoding="utf-8")

        return paths

    def __exit__(self, exc_type, exc, tb):
        self._tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
