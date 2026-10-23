"""Small OS integrations: clipboard text, opening and revealing files."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def _win_clipboard_api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    return user32, kernel32


def _open_clipboard(user32) -> bool:
    # Another app may hold the clipboard briefly; retry a few times.
    for _ in range(5):
        if user32.OpenClipboard(None):
            return True
        ctypes.windll.kernel32.Sleep(20)
    return False


def get_clipboard_text() -> str | None:
    if sys.platform.startswith("win"):
        try:
            user32, kernel32 = _win_clipboard_api()
            if not _open_clipboard(user32):
                return None
            try:
                handle = user32.GetClipboardData(_CF_UNICODETEXT)
                if not handle:
                    return None
                pointer = kernel32.GlobalLock(handle)
                if not pointer:
                    return None
                try:
                    return ctypes.wstring_at(pointer)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        except Exception:
            return None
    command = ["pbpaste"] if sys.platform == "darwin" else ["xclip", "-selection", "clipboard", "-o"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=False)
    except Exception:
        return None
    return result.stdout if result.returncode == 0 else None


def set_clipboard_text(text: str) -> bool:
    if sys.platform.startswith("win"):
        try:
            user32, kernel32 = _win_clipboard_api()
            data = ctypes.create_unicode_buffer(text)
            size = ctypes.sizeof(data)
            handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, size)
            if not handle:
                return False
            pointer = kernel32.GlobalLock(handle)
            ctypes.memmove(pointer, data, size)
            kernel32.GlobalUnlock(handle)
            if not _open_clipboard(user32):
                return False
            try:
                user32.EmptyClipboard()
                return bool(user32.SetClipboardData(_CF_UNICODETEXT, handle))
            finally:
                user32.CloseClipboard()
        except Exception:
            return False
    command = ["pbcopy"] if sys.platform == "darwin" else ["xclip", "-selection", "clipboard"]
    try:
        subprocess.run(command, input=text, text=True, timeout=2, check=True)
        return True
    except Exception:
        return False


def open_path(path: str) -> bool:
    """Open a file or folder with its default application."""
    target = Path(path)
    if not target.exists():
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return True
    except Exception:
        return False


def reveal_path(path: str) -> bool:
    """Show a file selected in the system file manager (or open its folder)."""
    target = Path(path)
    if not target.exists():
        return open_path(str(target.parent)) if target.parent.exists() else False
    try:
        if sys.platform.startswith("win"):
            if target.is_dir():
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer", f"/select,{target}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
        return True
    except Exception:
        return False


__all__ = ["get_clipboard_text", "open_path", "reveal_path", "set_clipboard_text"]
