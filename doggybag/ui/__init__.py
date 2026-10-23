"""UI package for Doggybag: a pywebview host for the HTML frontend in ``web/``.

Import ``doggybag.ui.webview_app`` lazily; pywebview pulls in native GUI
dependencies that headless callers (tests, ``--smoke``) do not need.
"""

from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parent / "web"

__all__ = ["WEB_ROOT"]
