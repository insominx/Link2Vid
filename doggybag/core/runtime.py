from __future__ import annotations

import os
import shutil
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
        return Path("__missing_appdata__") / "Doggybag" / "developer.json"
    return Path(appdata) / "Doggybag" / "developer.json"


def model_cache_dir() -> Path:
    """Return the shared Faster Whisper model cache root.

    Prefers ``%LOCALAPPDATA%\\Doggybag\\models``. When ``LOCALAPPDATA`` is
    unset, empty, or whitespace-only, falls back to ``~/.doggybag/models``.
    Source and frozen modes share this path; model weights are never bundled
    with the app.
    """
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        return Path(local_appdata) / "Doggybag" / "models"
    return Path.home() / ".doggybag" / "models"


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


LEGACY_APP_NAME = "Link2Vid"


def _legacy_data_moves() -> list[tuple[Path, Path, str]]:
    """``(old, new, kind)`` pairs for data stored under the pre-rename name."""
    moves: list[tuple[Path, Path, str]] = []
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    appdata = os.environ.get("APPDATA", "").strip()
    if local_appdata:
        moves.append(
            (Path(local_appdata) / LEGACY_APP_NAME / "models", model_cache_dir(), "dir")
        )
    else:
        moves.append((Path.home() / ".link2vid" / "models", model_cache_dir(), "dir"))
    if appdata:
        for name in ("settings.json", "developer.json"):
            moves.append(
                (Path(appdata) / LEGACY_APP_NAME / name, Path(appdata) / "Doggybag" / name, "file")
            )
    else:
        moves.append(
            (Path.home() / ".link2vid" / "settings.json", Path.home() / ".doggybag" / "settings.json", "file")
        )
    return moves


def migrate_legacy_data() -> list[str]:
    """Adopt settings and downloaded Whisper models from the Link2Vid name.

    Models are moved (a rename on the same volume, so multi-GB caches are not
    copied or re-downloaded); small settings files are copied. Nothing is
    overwritten, and failures leave the old data in place.
    """
    notes: list[str] = []
    for old, new, kind in _legacy_data_moves():
        if not old.exists():
            continue
        try:
            if kind == "dir":
                notes.extend(_merge_dir(old, new))
            elif not new.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, new)
                notes.append(f"Migrated {old} -> {new}")
        except OSError as exc:
            notes.append(f"Could not migrate {old}: {exc}")
    return notes


def _merge_dir(old: Path, new: Path) -> list[str]:
    """Move ``old`` to ``new``; if ``new`` exists, move over entries it lacks."""
    if not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        return [f"Migrated {old} -> {new}"]
    notes = []
    for child in old.iterdir():
        target = new / child.name
        if not target.exists():
            child.rename(target)
            notes.append(f"Migrated {child} -> {target}")
    try:
        old.rmdir()  # only succeeds once nothing is left behind
    except OSError:
        pass
    return notes


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
