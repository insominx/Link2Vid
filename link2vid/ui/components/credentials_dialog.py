from __future__ import annotations

from urllib.parse import urlparse

import customtkinter as ctk


def _site_label(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlparse(url).hostname or "").strip()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _center_toplevel(top: ctk.CTkToplevel, parent: ctk.CTk, width: int, height: int) -> None:
    top.update_idletasks()
    parent.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    top.geometry(f"{width}x{height}+{x}+{y}")


def prompt_credentials(
    parent: ctk.CTk,
    url: str | None = None,
    *,
    browser_automation: bool = True,
) -> tuple[str | None, str | None]:
    """Show a modal login dialog. Must run on the Tk main thread."""
    result: dict[str, str | None] = {"username": None, "password": None}

    top = ctk.CTkToplevel(parent)
    top.title("Login Required")
    width, height = 520, 340
    top.minsize(460, 300)
    top.transient(parent)
    top.grab_set()
    _center_toplevel(top, parent, width, height)

    container = ctk.CTkFrame(top)
    container.pack(fill="both", expand=True, padx=20, pady=20)
    container.grid_columnconfigure(0, weight=1)

    site = _site_label(url)
    if browser_automation:
        heading = f"Sign in to continue browser automation for {site}" if site else "Sign in to continue browser automation"
        detail = "Link2Vid will open the site in Chrome, log in with these credentials, then search for playable media."
    else:
        heading = f"Sign in to {site}" if site else "Sign in"
        detail = (
            "Link2Vid will sign in directly (no browser window), load the video, "
            "then sign out. Credentials are not saved."
        )
    ctk.CTkLabel(
        container,
        text=heading,
        anchor="w",
        justify="left",
        wraplength=460,
        font=ctk.CTkFont(size=15, weight="bold"),
    ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

    ctk.CTkLabel(
        container,
        text=detail,
        anchor="w",
        justify="left",
        wraplength=460,
        text_color="#94a3b8",
    ).grid(row=1, column=0, sticky="ew", pady=(0, 16))

    ctk.CTkLabel(container, text="Email or username", anchor="w").grid(row=2, column=0, sticky="ew", pady=(0, 4))
    username_entry = ctk.CTkEntry(container, placeholder_text="Email or username")
    username_entry.grid(row=3, column=0, sticky="ew", pady=(0, 12))

    ctk.CTkLabel(container, text="Password", anchor="w").grid(row=4, column=0, sticky="ew", pady=(0, 4))
    password_entry = ctk.CTkEntry(container, placeholder_text="Password", show="*")
    password_entry.grid(row=5, column=0, sticky="ew", pady=(0, 16))

    button_row = ctk.CTkFrame(container, fg_color="transparent")
    button_row.grid(row=6, column=0, sticky="e")
    button_row.grid_columnconfigure(0, weight=1)

    def cancel() -> None:
        top.destroy()

    def submit() -> None:
        username = username_entry.get().strip()
        password = password_entry.get()
        if not username or not password:
            return
        result["username"] = username
        result["password"] = password
        top.destroy()

    ctk.CTkButton(button_row, text="Cancel", width=110, fg_color="#475569", hover_color="#64748b", command=cancel).grid(
        row=0,
        column=0,
        padx=(0, 8),
    )
    ctk.CTkButton(button_row, text="Continue", width=120, command=submit).grid(row=0, column=1)

    top.protocol("WM_DELETE_WINDOW", cancel)
    top.bind("<Escape>", lambda _event: cancel())
    top.bind("<Return>", lambda _event: submit())

    username_entry.focus_set()
    parent.wait_window(top)
    return result["username"], result["password"]
