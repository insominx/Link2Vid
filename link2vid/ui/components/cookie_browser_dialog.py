from __future__ import annotations

import customtkinter as ctk

from ...core.cookie_prefs import COOKIE_BROWSERS, COOKIE_BROWSER_LABELS


def _center_toplevel(top: ctk.CTkToplevel, parent: ctk.CTk, width: int, height: int) -> None:
    top.update_idletasks()
    parent.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    top.geometry(f"{width}x{height}+{x}+{y}")


def prompt_cookie_browser(parent: ctk.CTk, *, suggested: str | None = None) -> str | None:
    """Ask which browser Link2Vid should read cookies from. Main thread only."""
    result: dict[str, str | None] = {"browser": None}

    top = ctk.CTkToplevel(parent)
    top.title("Which browser?")
    width, height = 520, 360
    top.minsize(460, 320)
    top.transient(parent)
    top.grab_set()
    _center_toplevel(top, parent, width, height)

    container = ctk.CTkFrame(top)
    container.pack(fill="both", expand=True, padx=20, pady=20)
    container.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        container,
        text="This site blocked the download without browser cookies.",
        anchor="w",
        justify="left",
        wraplength=460,
        font=ctk.CTkFont(size=15, weight="bold"),
    ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

    ctk.CTkLabel(
        container,
        text="Which browser should Link2Vid use automatically from now on?\n"
        "Close that browser first if it is running, then choose it.",
        anchor="w",
        justify="left",
        wraplength=460,
        text_color="#94a3b8",
    ).grid(row=1, column=0, sticky="ew", pady=(0, 16))

    def choose(browser: str) -> None:
        result["browser"] = browser
        top.destroy()

    def cancel() -> None:
        top.destroy()

    for index, browser in enumerate(COOKIE_BROWSERS):
        is_suggested = browser == suggested
        ctk.CTkButton(
            container,
            text=COOKIE_BROWSER_LABELS[browser],
            command=lambda selected=browser: choose(selected),
            fg_color="#1d4ed8" if is_suggested else "#334155",
            hover_color="#2563eb" if is_suggested else "#475569",
        ).grid(row=2 + index, column=0, sticky="ew", pady=4)

    ctk.CTkButton(
        container,
        text="Cancel",
        width=110,
        fg_color="#475569",
        hover_color="#64748b",
        command=cancel,
    ).grid(row=6, column=0, sticky="e", pady=(16, 0))

    top.protocol("WM_DELETE_WINDOW", cancel)
    top.bind("<Escape>", lambda _event: cancel())
    parent.wait_window(top)
    return result["browser"]
