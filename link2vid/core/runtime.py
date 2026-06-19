from __future__ import annotations

import os
import sys
from pathlib import Path

_bootstrap_done = False


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(sys.argv[0]).resolve().parent


def _appdata_developer_json() -> Path:
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return Path("__missing_appdata__") / "Link2Vid" / "developer.json"
    return Path(appdata) / "Link2Vid" / "developer.json"


def resolve_developer_json() -> Path | None:
    if is_frozen():
        candidates = [
            app_dir() / "developer.json",
            _appdata_developer_json(),
        ]
    else:
        candidates = [
            Path.cwd() / "developer.json",
            app_dir() / "developer.json",
            _appdata_developer_json(),
        ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def bootstrap_runtime() -> None:
    global _bootstrap_done
    if _bootstrap_done:
        return
    bin_dir = app_dir() / "bin"
    if bin_dir.is_dir():
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    _bootstrap_done = True


def startup_summary() -> str:
    dev_json = resolve_developer_json()
    dev_part = str(dev_json) if dev_json else "none"
    return f"runtime: frozen={is_frozen()} app_dir={app_dir()} developer.json={dev_part}"
