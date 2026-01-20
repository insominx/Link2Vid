"""Footer bar component with output/cookies/diagnostics controls."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk


def _noop() -> None:
    return None


class FooterBar(ctk.CTkFrame):
    def __init__(
        self,
        master,
        output_path: str = "",
        cookies_path: str = "",
        on_change_output: Callable[[], None] | None = None,
        on_change_cookies: Callable[[], None] | None = None,
        on_copy_diagnostics: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)

        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(3, weight=0)

        self.output_label = ctk.CTkLabel(self, text="Output:", font=("Arial", 12, "bold"))
        self.output_label.grid(row=0, column=0, sticky="w", padx=(12, 6), pady=(8, 4))

        self.output_value = ctk.CTkLabel(
            self,
            text=output_path or "Not set",
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.output_value.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=(8, 4))

        self.output_button = ctk.CTkButton(
            self,
            text="Change...",
            width=110,
            command=on_change_output or _noop,
        )
        self.output_button.grid(row=0, column=2, padx=(0, 12), pady=(8, 4))

        self.cookies_label = ctk.CTkLabel(self, text="Cookies:", font=("Arial", 12, "bold"))
        self.cookies_label.grid(row=1, column=0, sticky="w", padx=(12, 6), pady=(0, 8))

        self.cookies_value = ctk.CTkLabel(
            self,
            text=cookies_path or "None",
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.cookies_value.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=(0, 8))

        self.cookies_button = ctk.CTkButton(
            self,
            text="Select cookies.txt",
            width=160,
            command=on_change_cookies or _noop,
        )
        self.cookies_button.grid(row=1, column=2, padx=(0, 12), pady=(0, 8))

        self.diagnostics_button = ctk.CTkButton(
            self,
            text="Copy diagnostics",
            width=150,
            command=on_copy_diagnostics or _noop,
        )
        self.diagnostics_button.grid(row=0, column=3, rowspan=2, padx=(0, 12), pady=8, sticky="e")

    def set_output_path(self, path: str) -> None:
        self.output_value.configure(text=path or "Not set")

    def set_cookies_path(self, path: str) -> None:
        self.cookies_value.configure(text=path or "None")
