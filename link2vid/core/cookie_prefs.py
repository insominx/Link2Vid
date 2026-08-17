"""Preferred browser for automatic cookie lookup."""

from __future__ import annotations

import json
import os
from pathlib import Path

COOKIE_BROWSERS = ("brave", "chrome", "edge", "firefox")
COOKIE_BROWSER_LABELS = {
    "brave": "Brave",
    "chrome": "Chrome",
    "edge": "Edge",
    "firefox": "Firefox",
}
AUTO_COOKIES_BROWSER_LABEL = "Auto-detect"


def normalize_cookies_browser(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if text in COOKIE_BROWSERS:
        return text
    return None


def cookies_browser_label(browser: str | None) -> str:
    normalized = normalize_cookies_browser(browser)
    if normalized is None:
        return AUTO_COOKIES_BROWSER_LABEL
    return COOKIE_BROWSER_LABELS[normalized]


def cookies_browser_choices() -> list[tuple[str, str | None]]:
    return [
        (AUTO_COOKIES_BROWSER_LABEL, None),
        *[(COOKIE_BROWSER_LABELS[name], name) for name in COOKIE_BROWSERS],
    ]


def user_settings_path() -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "Link2Vid" / "settings.json"
    return Path.home() / ".link2vid" / "settings.json"


def load_user_settings(path: Path | None = None) -> dict:
    settings_file = path or user_settings_path()
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_user_settings(settings: dict, path: Path | None = None) -> None:
    settings_file = path or user_settings_path()
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(
        json.dumps(settings, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_preferred_cookies_browser(
    *,
    settings: dict | None = None,
    dev_defaults: dict | None = None,
    path: Path | None = None,
) -> str | None:
    data = settings if settings is not None else load_user_settings(path)
    preferred = normalize_cookies_browser((data or {}).get("cookies_browser"))
    if preferred:
        return preferred
    return normalize_cookies_browser((dev_defaults or {}).get("cookies_browser"))


def store_preferred_cookies_browser(
    browser: str | None,
    *,
    path: Path | None = None,
) -> str | None:
    settings_file = path or user_settings_path()
    settings = load_user_settings(settings_file)
    normalized = normalize_cookies_browser(browser)
    if normalized:
        settings["cookies_browser"] = normalized
    else:
        settings.pop("cookies_browser", None)
    save_user_settings(settings, settings_file)
    return normalized


def load_preferred_cookies_path(
    *,
    settings: dict | None = None,
    dev_defaults: dict | None = None,
    path: Path | None = None,
) -> str | None:
    data = settings if settings is not None else load_user_settings(path)
    stored = (data or {}).get("cookies_path")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    dev_value = (dev_defaults or {}).get("cookies_path")
    if isinstance(dev_value, str) and dev_value.strip():
        return dev_value.strip()
    return None


def store_preferred_cookies_path(
    cookies_path: str | None,
    *,
    path: Path | None = None,
) -> str | None:
    settings_file = path or user_settings_path()
    settings = load_user_settings(settings_file)
    normalized = cookies_path.strip() if isinstance(cookies_path, str) and cookies_path.strip() else None
    if normalized:
        settings["cookies_path"] = normalized
    else:
        settings.pop("cookies_path", None)
    save_user_settings(settings, settings_file)
    return normalized
