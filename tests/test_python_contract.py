"""Python 3.10+ source/run/build contract and headless smoke import checks."""

from __future__ import annotations

import ast
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_BAT = ROOT / "setup_doggybag.bat"
RUN_BAT = ROOT / "run_doggybag.bat"
BUILD_BAT = ROOT / "build_windows.bat"
CHECK_PYTHON_BAT = ROOT / "packaging" / "check_python.bat"
ENTRY = ROOT / "video_downloader.py"
REQUIREMENTS = ROOT / "requirements.txt"

MIN_PYTHON = (3, 10)
MIN_PYTHON_LABEL = "3.10+"

REQUIRED_SMOKE_MODULES = (
    "webview",
    "yt_dlp",
    "yt_dlp_ejs",
    "faster_whisper",
    "ctranslate2",
    "av",
    "onnxruntime",
    "tokenizers",
    "huggingface_hub",
)

# Modules that must import on Python 3.10 (postponed annotations where needed).
PYTHON310_IMPORT_TARGETS = (
    ROOT / "doggybag" / "app" / "controller.py",
    ROOT / "doggybag" / "core" / "whisper_transcription.py",
    ROOT / "doggybag" / "core" / "audio_source.py",
    ROOT / "doggybag" / "core" / "runtime.py",
    ROOT / "doggybag" / "core" / "diagnostics.py",
    ROOT / "doggybag" / "app" / "viewmodels.py",
)

PYTHON310_GRAMMAR_TARGETS = (
    ENTRY,
    ROOT / "doggybag" / "core" / "__init__.py",
    ROOT / "doggybag" / "core" / "audio_source.py",
    ROOT / "doggybag" / "core" / "diagnostics.py",
    ROOT / "doggybag" / "core" / "runtime.py",
    ROOT / "doggybag" / "core" / "whisper_transcription.py",
    ROOT / "doggybag" / "app" / "controller.py",
    ROOT / "doggybag" / "app" / "viewmodels.py",
    ROOT / "doggybag" / "app" / "desktop.py",
    ROOT / "doggybag" / "ui" / "webview_app.py",
)

PYTHON310_IMPORT_MODULES = (
    "video_downloader",
    "doggybag.core",
    "doggybag.core.audio_source",
    "doggybag.core.diagnostics",
    "doggybag.core.runtime",
    "doggybag.core.whisper_transcription",
    "doggybag.app.controller",
    "doggybag.app.viewmodels",
    "doggybag.ui.webview_app",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_check_python(
    candidate: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = f'call "{CHECK_PYTHON_BAT}" "{candidate}"'
    return subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
        executable=os.environ.get("COMSPEC", "cmd.exe"),
        shell=True,
    )


def _record_probe(
    label: str,
    candidate: str,
    completed: subprocess.CompletedProcess[str],
) -> None:
    output = (completed.stdout + completed.stderr).strip() or "<none>"
    print(
        f"[cmd-probe] {label}: candidate={candidate!r}; "
        f"exit={completed.returncode}; output={output!r}"
    )


def _find_python310_commands() -> list[list[str]]:
    """Candidate commands that may launch a real Python 3.10 interpreter."""
    candidates: list[list[str]] = []
    if shutil.which("py"):
        candidates.append(["py", "-3.10"])
    for name in ("python3.10", "python310"):
        path = shutil.which(name)
        if path:
            candidates.append([path])
    return candidates


def _probe_python310() -> tuple[list[str] | None, str]:
    """Return (argv, note) for a live 3.10 interpreter, or (None, reason)."""
    last_error = "no candidate launcher found"
    for cmd in _find_python310_commands():
        try:
            completed = subprocess.run(
                [*cmd, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = f"{cmd!r}: {exc}"
            continue
        if completed.returncode != 0:
            last_error = (
                f"{cmd!r} exit {completed.returncode}: "
                f"{(completed.stderr or completed.stdout).strip()}"
            )
            continue
        version = (completed.stdout or "").strip()
        if version.startswith("3.10"):
            return cmd, f"live Python 3.10 available via {' '.join(cmd)} ({version})"
        last_error = f"{cmd!r} reported {version!r}, not 3.10"
    return None, f"live Python 3.10 unavailable ({last_error})"


class TestPythonContract(unittest.TestCase):
    def test_minimum_version_constant_is_310(self):
        self.assertEqual(MIN_PYTHON, (3, 10))
        self.assertEqual(MIN_PYTHON_LABEL, "3.10+")

    def test_minimum_matches_current_yt_dlp_requirement(self):
        requirements = _read(REQUIREMENTS)
        self.assertIn("yt-dlp[default]>=2026.03.13", requirements)
        self.assertGreaterEqual(MIN_PYTHON, (3, 10))

    def test_scripts_exist(self):
        for path in (SETUP_BAT, RUN_BAT, BUILD_BAT, CHECK_PYTHON_BAT, ENTRY):
            self.assertTrue(path.is_file(), f"missing {path}")

    def test_shared_helper_has_distinct_failures_and_minimum(self):
        text = _read(CHECK_PYTHON_BAT)
        self.assertIn("sys.version_info >= (3, 10)", text)
        self.assertIn("Python 3.10+", text)
        self.assertIn("interpreter not found", text)
        self.assertIn("command not found on PATH", text)
        self.assertIn("older than 3.10", text)
        self.assertIn("exit /b 1", text)
        self.assertIn("exit /b 2", text)
        self.assertIn("exit /b 3", text)
        self.assertLess(text.find("if defined PATH_SHAPED"), text.find('where "%PYTHON_CANDIDATE%"'))

    def test_check_python_accepts_absolute_valid_interpreter(self):
        candidate = str(Path(sys.executable).resolve())
        completed = _run_check_python(candidate)
        _record_probe("absolute-valid", candidate, completed)
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def test_check_python_reports_missing_absolute_path(self):
        missing = ROOT / "missing interpreter" / "python.exe"
        completed = _run_check_python(str(missing))
        _record_probe("absolute-missing", str(missing), completed)
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 1, output)
        self.assertIn("interpreter not found", output)
        self.assertIn(str(missing), output)
        self.assertNotIn("older than 3.10", output)

    def test_check_python_reports_bare_old_command_on_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "doggybag_fake_old_python.cmd"
            fake.write_text("@echo off\r\nexit /b 39\r\n", encoding="ascii")
            env = os.environ.copy()
            env["PATH"] = f"{tmp}{os.pathsep}{env.get('PATH', '')}"
            completed = _run_check_python("doggybag_fake_old_python", env=env)

        _record_probe("bare-old-on-path", "doggybag_fake_old_python", completed)
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 2, output)
        self.assertIn("Python 3.10+ is required", output)
        self.assertIn("older than 3.10", output)
        self.assertNotIn("not found", output)

    def test_check_python_accepts_interpreter_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            spaced_dir = Path(tmp) / "interpreter path with spaces"
            spaced_dir.mkdir()
            wrapper = spaced_dir / "python wrapper.cmd"
            wrapper.write_text(
                f'@echo off\r\n"{sys.executable}" %*\r\n',
                encoding="utf-8",
            )
            completed = _run_check_python(str(wrapper))
            _record_probe("absolute-spaced-valid", str(wrapper), completed)

        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def test_setup_gate_before_pip_and_quotes_interpreter(self):
        text = _read(SETUP_BAT)
        self.assertNotIn("Python 3.8+", text)
        self.assertIn('call "%SCRIPT_DIR%packaging\\check_python.bat" "%PYTHON%"', text)
        self.assertNotIn('where "%PYTHON%"', text)
        self.assertIn('"%PYTHON%" -m pip install', text)

        gate_idx = text.find("check_python.bat")
        pip_idx = text.find("pip install")
        self.assertGreater(gate_idx, 0)
        self.assertGreater(pip_idx, gate_idx, "version gate must run before pip install")
        self.assertIn("Setup stopped before dependency installation", text)
        venv_idx = text.find(".venv\\Scripts\\python.exe")
        self.assertGreater(venv_idx, 0)
        self.assertGreater(gate_idx, venv_idx)

    def test_run_gate_before_app_import_and_checks_pythonw_by_kind(self):
        text = _read(RUN_BAT)
        self.assertNotIn("Python 3.8+", text)
        self.assertIn('call "%SCRIPT_DIR%packaging\\check_python.bat" "%PYTHON%"', text)
        self.assertNotIn('where "%PYTHON%"', text)

        gate_idx = text.find("check_python.bat")
        import_idx = text.find("from doggybag.ui.webview_app import run")
        self.assertGreater(gate_idx, 0)
        self.assertGreater(import_idx, gate_idx, "version gate must run before app import")
        self.assertIn("Doggybag was not launched", text)
        self.assertIn("pause", text)
        venv_idx = text.find(".venv\\Scripts\\python.exe")
        self.assertGreater(venv_idx, 0)
        self.assertGreater(gate_idx, venv_idx)
        self.assertIn("pathlib.Path(sys.executable).with_name('pythonw.exe')", text)
        self.assertIn('if not exist "%PYTHONW%" set "PYTHONW="', text)
        self.assertNotIn("where pythonw", text)
        self.assertNotIn('where "%PYTHONW%"', text)
        self.assertIn("if defined PYTHONW", text)

    def test_build_gate_before_pip_and_pyinstaller(self):
        text = _read(BUILD_BAT)
        self.assertIn('call "%SCRIPT_DIR%packaging\\check_python.bat" "%PYTHON%"', text)
        self.assertNotIn('where "%PYTHON%"', text)

        gate_idx = text.find("check_python.bat")
        pip_idx = text.find("pip install")
        pyinstaller_idx = text.find("PyInstaller")
        self.assertGreater(gate_idx, 0)
        self.assertGreater(pip_idx, gate_idx, "version gate must run before build pip install")
        self.assertGreater(
            pyinstaller_idx,
            gate_idx,
            "version gate must run before PyInstaller",
        )
        self.assertIn("Build stopped before dependency installation and PyInstaller", text)
        venv_idx = text.find(".venv\\Scripts\\python.exe")
        self.assertGreater(venv_idx, 0)
        self.assertGreater(gate_idx, venv_idx)
        self.assertNotIn("EnableDelayedExpansion", text)

    def test_build_gate_preserves_script_dir_containing_bang(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe_root = Path(tmp) / "Doggybag!gateprobe"
            packaging_dir = probe_root / "packaging"
            fake_bin = Path(tmp) / "fake-bin"
            packaging_dir.mkdir(parents=True)
            fake_bin.mkdir()
            shutil.copy2(BUILD_BAT, probe_root / BUILD_BAT.name)
            shutil.copy2(CHECK_PYTHON_BAT, packaging_dir / CHECK_PYTHON_BAT.name)
            (fake_bin / "python.cmd").write_text(
                "@echo off\r\nexit /b 39\r\n",
                encoding="ascii",
            )
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

            completed = subprocess.run(
                [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "build_windows.bat"],
                cwd=str(probe_root),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=env,
            )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 2, output)
        self.assertIn("older than 3.10", output)
        self.assertNotIn("Installing build dependencies", output)
        self.assertNotIn("Building portable app", output)

    def test_scripts_share_one_python_gate(self):
        for path in (SETUP_BAT, RUN_BAT, BUILD_BAT):
            text = _read(path)
            self.assertEqual(text.count("check_python.bat"), 1, path.name)
            self.assertNotIn("3.8+", text, path.name)
            self.assertNotRegex(
                text,
                r"(?m)^\s*%PYTHON%\s",
                f"{path.name} has an unquoted interpreter invocation",
            )

    def test_postponed_annotations_on_python310_targets(self):
        for path in PYTHON310_IMPORT_TARGETS:
            self.assertTrue(path.is_file(), f"missing {path}")
            tree = ast.parse(
                _read(path),
                filename=str(path),
                feature_version=MIN_PYTHON,
            )
            futures = [
                node
                for node in tree.body
                if isinstance(node, ast.ImportFrom)
                and node.module == "__future__"
                and any(alias.name == "annotations" for alias in node.names)
            ]
            self.assertTrue(
                futures,
                f"{path.relative_to(ROOT)} must use from __future__ import annotations",
            )
            # Future import must appear before other non-docstring statements.
            first_stmt = tree.body[0]
            if (
                isinstance(first_stmt, ast.Expr)
                and isinstance(first_stmt.value, ast.Constant)
                and isinstance(first_stmt.value.value, str)
            ):
                first_code = tree.body[1]
            else:
                first_code = first_stmt
            self.assertIsInstance(first_code, ast.ImportFrom)
            self.assertEqual(first_code.module, "__future__")

    def test_import_targets_parse_with_python310_grammar(self):
        for path in PYTHON310_GRAMMAR_TARGETS:
            self.assertTrue(path.is_file(), f"missing {path}")
            try:
                ast.parse(
                    _read(path),
                    filename=str(path),
                    feature_version=MIN_PYTHON,
                )
            except SyntaxError as exc:
                self.fail(
                    f"{path.relative_to(ROOT)} is not Python 3.10 grammar-compatible: {exc}"
                )

    def test_import_targets_compile_with_current_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, path in enumerate(PYTHON310_GRAMMAR_TARGETS):
                try:
                    py_compile.compile(
                        str(path),
                        cfile=str(Path(tmp) / f"target-{index}.pyc"),
                        doraise=True,
                    )
                except py_compile.PyCompileError as exc:
                    self.fail(
                        f"{path.relative_to(ROOT)} failed current py_compile: {exc}"
                    )

    def test_view_models_are_in_static_and_live_import_audits(self):
        viewmodels = ROOT / "doggybag" / "app" / "viewmodels.py"
        self.assertIn(viewmodels, PYTHON310_IMPORT_TARGETS)
        self.assertIn(viewmodels, PYTHON310_GRAMMAR_TARGETS)
        self.assertIn(
            "doggybag.app.viewmodels",
            PYTHON310_IMPORT_MODULES,
        )

    def test_smoke_import_list_covers_whisper_closure(self):
        import video_downloader as entry

        self.assertEqual(tuple(entry.SMOKE_IMPORTS), REQUIRED_SMOKE_MODULES)
        source = _read(ENTRY)
        for name in REQUIRED_SMOKE_MODULES:
            self.assertIn(name, source)
        self.assertIn("doggybag.ui.webview_app", source)
        # Must remain headless: no UI mainloop and no model construction/download.
        smoke_fn = None
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_run_smoke":
                smoke_fn = node
                break
        self.assertIsNotNone(smoke_fn, "expected _run_smoke helper")
        smoke_src = ast.get_source_segment(source, smoke_fn) or ""
        for banned in (
            "mainloop",
            "WhisperModel",
            "download_model",
            "CTk()",
            "transcribe(",
        ):
            self.assertNotIn(banned, smoke_src)

    def test_smoke_helper_reports_failed_module(self):
        import video_downloader as entry

        original = entry.SMOKE_IMPORTS
        try:
            entry.SMOKE_IMPORTS = ("definitely_missing_doggybag_smoke_module_xyz",)
            with self.assertRaises(SystemExit) as ctx:
                entry._run_smoke()
            self.assertEqual(ctx.exception.code, 1)
        finally:
            entry.SMOKE_IMPORTS = original

    def test_live_python310_import_smoke_or_record_unavailable(self):
        cmd, note = _probe_python310()
        if cmd is None:
            # Do not claim the live 3.10 gate ran when the interpreter is absent.
            self.assertIn("unavailable", note)
            print(f"[python-contract] {note}")
            self.skipTest(note)

        import_stmt = "; ".join(f"import {name}" for name in PYTHON310_IMPORT_MODULES)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run(
            [*cmd, "-c", import_stmt],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=env,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"Python 3.10 import smoke failed via {' '.join(cmd)}:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        print(f"[python-contract] {note}; import smoke passed")


class TestCurrentInterpreterMeetsMinimum(unittest.TestCase):
    def test_running_interpreter_is_at_least_39(self):
        self.assertGreaterEqual(
            sys.version_info[:2],
            MIN_PYTHON,
            f"test runner must be Python {MIN_PYTHON_LABEL}; got {sys.version}",
        )


if __name__ == "__main__":
    unittest.main()
