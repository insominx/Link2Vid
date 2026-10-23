"""The boundary between the controller and whatever renders it."""

from __future__ import annotations

from concurrent.futures import Future
from typing import Protocol


class ViewPort(Protocol):
    """Services the controller needs from a frontend.

    ``emit`` publishes state; ``request`` opens a dialog and resolves the
    returned future with the user's answer. Both must be safe to call from any
    thread. File pickers block the calling thread until the user closes them.
    """

    def emit(self, event: str, payload: dict) -> None: ...

    def request(self, kind: str, payload: dict) -> Future: ...

    def notify(self, level: str, title: str, message: str = "") -> None: ...

    def set_clipboard(self, text: str) -> bool: ...

    def get_clipboard(self) -> str | None: ...

    def pick_folder(self, initial: str | None = None) -> str | None: ...

    def pick_file(self, title: str, initial: str | None = None) -> str | None: ...


class NullView:
    """Headless view: drops events and answers every dialog with ``None``."""

    def emit(self, event: str, payload: dict) -> None:
        return None

    def request(self, kind: str, payload: dict) -> Future:
        future: Future = Future()
        future.set_result(None)
        return future

    def notify(self, level: str, title: str, message: str = "") -> None:
        return None

    def set_clipboard(self, text: str) -> bool:
        return False

    def get_clipboard(self) -> str | None:
        return None

    def pick_folder(self, initial: str | None = None) -> str | None:
        return None

    def pick_file(self, title: str, initial: str | None = None) -> str | None:
        return None


__all__ = ["NullView", "ViewPort"]
