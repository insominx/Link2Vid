import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from doggybag.core import runtime


class TestRuntime(unittest.TestCase):
    def setUp(self):
        runtime._bootstrap_done = False

    def test_app_dir_dev_uses_entry_script_parent(self):
        entry = Path(__file__).resolve().parents[1] / "video_downloader.py"
        with mock.patch.object(sys, "argv", [str(entry)]):
            with mock.patch.object(runtime, "is_frozen", return_value=False):
                self.assertEqual(runtime.app_dir(), entry.parent)

    def test_app_dir_frozen_uses_exe_parent_not_meipass(self):
        exe = Path("C:/Apps/Doggybag/Doggybag.exe")
        with mock.patch.object(runtime, "is_frozen", return_value=True):
            with mock.patch.object(sys, "executable", str(exe)):
                with mock.patch.object(sys, "_MEIPASS", "C:/Apps/Doggybag/_internal", create=True):
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

    def test_model_cache_dir_prefers_localappdata(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\demo\AppData\Local"}, clear=False):
            self.assertEqual(
                runtime.model_cache_dir(),
                Path(r"C:\Users\demo\AppData\Local") / "Doggybag" / "models",
            )

    def test_model_cache_dir_falls_back_to_home_when_localappdata_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "LOCALAPPDATA"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(runtime.Path, "home", return_value=Path("/home/demo")):
                self.assertEqual(
                    runtime.model_cache_dir(),
                    Path("/home/demo") / ".doggybag" / "models",
                )

    def test_model_cache_dir_falls_back_when_localappdata_empty(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": ""}, clear=False):
            with mock.patch.object(runtime.Path, "home", return_value=Path("D:/Users/demo")):
                self.assertEqual(
                    runtime.model_cache_dir(),
                    Path("D:/Users/demo") / ".doggybag" / "models",
                )

    def test_model_cache_dir_falls_back_when_localappdata_is_whitespace(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": " \t\r\n "}, clear=False):
            with mock.patch.object(runtime.Path, "home", return_value=Path("D:/Users/demo")):
                self.assertEqual(
                    runtime.model_cache_dir(),
                    Path("D:/Users/demo") / ".doggybag" / "models",
                )


class TestLegacyMigration(unittest.TestCase):
    """Data saved under the old Link2Vid name is adopted after the rename."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.local = self.root / "Local"
        self.roaming = self.root / "Roaming"
        env = {"LOCALAPPDATA": str(self.local), "APPDATA": str(self.roaming)}
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _legacy(self):
        models = self.local / "Link2Vid" / "models" / "models--demo"
        models.mkdir(parents=True)
        (models / "weights.bin").write_bytes(b"weights")
        settings = self.roaming / "Link2Vid" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"cookies_browser": "firefox"}', encoding="utf-8")

    def test_models_move_and_settings_copy(self):
        self._legacy()
        notes = runtime.migrate_legacy_data()
        new_models = self.local / "Doggybag" / "models"
        self.assertEqual((new_models / "models--demo" / "weights.bin").read_bytes(), b"weights")
        self.assertFalse((self.local / "Link2Vid" / "models").exists())
        self.assertEqual(
            (self.roaming / "Doggybag" / "settings.json").read_text(encoding="utf-8"),
            '{"cookies_browser": "firefox"}',
        )
        self.assertTrue((self.roaming / "Link2Vid" / "settings.json").exists())
        self.assertEqual(len(notes), 2)

    def test_existing_new_settings_are_never_overwritten(self):
        self._legacy()
        new_settings = self.roaming / "Doggybag" / "settings.json"
        new_settings.parent.mkdir(parents=True)
        new_settings.write_text("{}", encoding="utf-8")
        runtime.migrate_legacy_data()
        self.assertEqual(new_settings.read_text(encoding="utf-8"), "{}")

    def test_models_merge_into_an_existing_new_cache(self):
        self._legacy()
        newer = self.local / "Doggybag" / "models" / "models--small"
        newer.mkdir(parents=True)
        clash = self.local / "Link2Vid" / "models" / "models--small"
        clash.mkdir(parents=True)
        (clash / "old.bin").write_bytes(b"old")
        runtime.migrate_legacy_data()
        merged = self.local / "Doggybag" / "models"
        self.assertTrue((merged / "models--demo" / "weights.bin").exists())
        self.assertFalse((merged / "models--small" / "old.bin").exists())
        self.assertTrue(clash.exists())

    def test_nothing_to_migrate_is_a_no_op(self):
        self.assertEqual(runtime.migrate_legacy_data(), [])
        self.assertFalse((self.local / "Doggybag").exists())


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
            "appdata": root / "appdata" / "Doggybag" / "developer.json",
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
