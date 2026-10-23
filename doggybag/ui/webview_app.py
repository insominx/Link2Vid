"""pywebview host for the HTML frontend in ``doggybag/ui/web``.

Threads:
- the GUI thread runs ``webview.start`` (WinForms/WebView2 on Windows);
- the controller dispatcher thread owns all application state;
- a flush thread batches emitted events into ``window.DBG.receive(...)``;
- pywebview runs each ``Bridge`` call on its own worker thread.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
import webbrowser
from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path

from ..app import desktop

WEB_ROOT = Path(__file__).resolve().parent / "web"

# Window/background colors must match the --bg tokens in web/styles.css.
DARK_BACKGROUND = "#0c0d11"
LIGHT_BACKGROUND = "#f5f6f8"

# Events whose latest payload supersedes earlier ones in the same flush.
_COALESCED = {"progress", "batch", "fetching", "settings", "results_state", "health", "url"}
_FLUSH_INTERVAL = 1 / 30


def system_prefers_dark() -> bool:
    if not sys.platform.startswith("win"):
        return True
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return True


def _colorref(hex_color: str) -> int:
    value = hex_color.lstrip("#")
    red, green, blue = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return red | (green << 8) | (blue << 16)


def style_title_bar(hwnd: int, dark: bool) -> None:
    """Match the native Windows 11 title bar to the page background."""
    if not sys.platform.startswith("win") or not hwnd:
        return
    import ctypes

    dwm = ctypes.windll.dwmapi
    dwm.DwmSetWindowAttribute.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]

    def set_attribute(attribute: int, value: int) -> None:
        data = ctypes.c_int(value)
        dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))

    background = DARK_BACKGROUND if dark else LIGHT_BACKGROUND
    set_attribute(20, 1 if dark else 0)  # DWMWA_USE_IMMERSIVE_DARK_MODE
    set_attribute(35, _colorref(background))  # DWMWA_CAPTION_COLOR (Windows 11)
    set_attribute(36, _colorref("#eceef3" if dark else "#0f1219"))  # DWMWA_TEXT_COLOR


class WebViewPresenter:
    """``ViewPort`` implementation that streams events to the page."""

    def __init__(self) -> None:
        self.window = None
        self._lock = threading.Lock()
        self._pending: OrderedDict = OrderedDict()
        self._sequence = 0
        self._wake = threading.Event()
        self._page_ready = threading.Event()
        self._closed = threading.Event()
        self._dialogs: dict[str, Future] = {}
        self._flusher = threading.Thread(target=self._flush_loop, name="dbg-flush", daemon=True)
        self._flusher.start()

    # ── ViewPort ────────────────────────────────────────────
    def emit(self, event: str, payload: dict) -> None:
        with self._lock:
            if event in _COALESCED:
                key = (event,)
            elif event == "card":
                key = ("card", payload.get("key"))
            else:
                self._sequence += 1
                key = (event, self._sequence)
            # Re-append so a superseding update lands after structural events
            # (for example a card update after the cards_add that created it).
            self._pending.pop(key, None)
            self._pending[key] = {"t": event, "p": payload}
        self._wake.set()

    def request(self, kind: str, payload: dict) -> Future:
        future: Future = Future()
        if self._closed.is_set():
            future.set_result(None)
            return future
        dialog_id = uuid.uuid4().hex
        with self._lock:
            self._dialogs[dialog_id] = future
        self.emit("dialog", {"id": dialog_id, "kind": kind, **payload})
        return future

    def resolve(self, dialog_id: str, answer) -> None:
        with self._lock:
            future = self._dialogs.pop(str(dialog_id), None)
        if future is not None and not future.done():
            future.set_result(answer)

    def notify(self, level: str, title: str, message: str = "") -> None:
        self.emit("toast", {"level": level, "title": title, "message": message})

    def set_clipboard(self, text: str) -> bool:
        return desktop.set_clipboard_text(text)

    def get_clipboard(self) -> str | None:
        return desktop.get_clipboard_text()

    def pick_folder(self, initial: str | None = None) -> str | None:
        import webview

        if self.window is None:
            return None
        result = self.window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=initial or "",
        )
        return _first(result)

    def pick_file(self, title: str, initial: str | None = None) -> str | None:
        import webview

        if self.window is None:
            return None
        directory = str(Path(initial).parent) if initial else ""
        result = self.window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=directory,
            file_types=("Cookie files (*.txt)", "All files (*.*)"),
        )
        return _first(result)

    # ── lifecycle ───────────────────────────────────────────
    def page_ready(self) -> None:
        """Drop events already covered by the boot snapshot and start flushing."""
        with self._lock:
            self._pending.clear()
        self._page_ready.set()

    def close(self) -> None:
        self._closed.set()
        self._wake.set()
        with self._lock:
            dialogs = list(self._dialogs.values())
            self._dialogs.clear()
        for future in dialogs:
            if not future.done():
                future.set_result(None)

    def _flush_loop(self) -> None:
        while not self._closed.is_set():
            self._wake.wait()
            if self._closed.is_set():
                return
            if not self._page_ready.is_set():
                self._page_ready.wait(0.25)
                continue
            self._wake.clear()
            with self._lock:
                batch = list(self._pending.values())
                self._pending.clear()
            if batch and self.window is not None:
                script = "window.DBG && window.DBG.receive(" + json.dumps(batch, default=str) + ")"
                try:
                    self.window.run_js(script)
                except Exception:
                    pass
            self._closed.wait(_FLUSH_INTERVAL)


def _first(result) -> str | None:
    if not result:
        return None
    if isinstance(result, (list, tuple)):
        return str(result[0]) if result else None
    return str(result)


class Bridge:
    """Methods callable from the page as ``window.pywebview.api.<name>``.

    Attributes are underscore-prefixed so pywebview does not expose them.
    """

    def __init__(self, presenter: WebViewPresenter, controller_ready: threading.Event) -> None:
        self._presenter = presenter
        self._controller_ready = controller_ready
        self._controller = None

    def _attach(self, controller) -> None:
        self._controller = controller

    def _app(self):
        self._controller_ready.wait()
        return self._controller

    def _post(self, func) -> None:
        self._app().post(func)

    # ── startup ─────────────────────────────────────────────
    def ready(self):
        app = self._app()

        def snapshot():
            self._presenter.page_ready()
            return app.boot_state()

        return app.run_on_ui_thread(snapshot)

    def set_theme(self, dark):
        window = self._presenter.window
        try:
            style_title_bar(window.native.Handle.ToInt64(), bool(dark))
        except Exception:
            pass

    # ── fetch / results ─────────────────────────────────────
    def set_url(self, text):
        self._post(lambda: self._app().url_entry.set(str(text or ""), publish=False))

    def fetch(self, url):
        self._post(lambda: self._app().fetch_videos(str(url or "")))

    def load_more(self):
        self._post(lambda: self._app().load_more())

    def card(self, generation, index, intent, value=None):
        self._post(lambda: self._app().card_intent(int(generation), int(index), str(intent), value))

    def select_all(self):
        self._post(lambda: self._app().select_all_entries())

    def select_none(self):
        self._post(lambda: self._app().select_no_entries())

    def batch(self, kind):
        if kind == "transcript":
            self._post(lambda: self._app().start_batch_transcript())
        else:
            self._post(lambda: self._app().start_batch_media())

    def cancel_batch(self):
        self._post(lambda: self._app().cancel_batch())

    def resolve_dialog(self, dialog_id, answer=None):
        self._presenter.resolve(dialog_id, answer)

    def clipboard_url(self):
        return self._app().clipboard_url()

    # ── settings ────────────────────────────────────────────
    def choose_folder(self):
        app = self._app()
        folder = self._presenter.pick_folder(app.output_path)
        if folder:
            self._post(lambda: app.set_output_folder(folder))

    def set_cookies_browser(self, browser=None):
        self._post(lambda: self._app().set_cookies_browser(browser or None))

    def choose_cookies_file(self):
        app = self._app()
        path = self._presenter.pick_file("Select cookies.txt", app.cookies_path)
        if path:
            self._post(lambda: app.set_cookies_file(path))

    def clear_cookies_file(self):
        self._post(lambda: self._app().set_cookies_file(None))

    def copy_diagnostics(self):
        self._post(lambda: self._app().copy_diagnostics())

    # ── shell ───────────────────────────────────────────────
    def open_folder(self):
        return desktop.open_path(self._app().output_path)

    def open_log(self):
        return desktop.open_path(self._app().debug_log_path)

    def open_output(self, path, reveal=False):
        if not self._is_known_output(path):
            return False
        return desktop.reveal_path(path) if reveal else desktop.open_path(path)

    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            webbrowser.open(url)
            return True
        return False

    def copy_text(self, text):
        return desktop.set_clipboard_text(str(text or ""))

    def _is_known_output(self, path) -> bool:
        app = self._app()
        target = str(path or "")
        if not target:
            return False
        return any(target in getattr(card, "outputs", ()) for card in list(app.cards))


def _icon_path() -> str | None:
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "doggybag.ico" if getattr(sys, "frozen", False) else None,
        Path(__file__).resolve().parents[2] / "doggybag.ico",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)
    return None


def _storage_path() -> str:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(base) / "Doggybag" if base else Path.home() / ".doggybag"
    path = root / "webview"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def run(*, debug: bool = False, startup_notes: list[str] | None = None) -> int:
    import webview

    from ..app import AppController

    presenter = WebViewPresenter()
    controller_ready = threading.Event()
    bridge = Bridge(presenter, controller_ready)
    holder: dict = {}

    def dispatcher() -> None:
        controller = AppController(presenter)
        for note in startup_notes or []:
            controller.log(note)
        holder["controller"] = controller
        bridge._attach(controller)
        controller_ready.set()
        controller.run_forever()

    threading.Thread(target=dispatcher, name="dbg-dispatcher", daemon=True).start()

    dark = system_prefers_dark()
    window = webview.create_window(
        "Doggybag",
        url=str(WEB_ROOT / "index.html"),
        js_api=bridge,
        width=1180,
        height=820,
        min_size=(880, 620),
        background_color=DARK_BACKGROUND if dark else LIGHT_BACKGROUND,
        text_select=True,
    )
    presenter.window = window

    def on_shown() -> None:
        try:
            style_title_bar(window.native.Handle.ToInt64(), dark)
        except Exception:
            pass

    def on_closed() -> None:
        presenter.close()
        controller = holder.get("controller")
        if controller is not None:
            controller.on_close()

    window.events.shown += on_shown
    window.events.closed += on_closed

    webview.start(
        debug=debug or bool(os.environ.get("DOGGYBAG_DEVTOOLS")),
        http_server=True,
        private_mode=False,
        storage_path=_storage_path(),
        icon=_icon_path(),
    )
    return 0


__all__ = ["Bridge", "WebViewPresenter", "run", "style_title_bar", "system_prefers_dark"]
