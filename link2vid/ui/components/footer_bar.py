"""Footer bar component with output/cookies/diagnostics controls."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from ...core.cookie_prefs import (
    AUTO_COOKIES_BROWSER_LABEL,
    cookies_browser_choices,
    cookies_browser_label,
    normalize_cookies_browser,
)


def _noop(*_args, **_kwargs) -> None:
    return None


class FooterBar(ctk.CTkFrame):
    def __init__(
        self,
        master,
        output_path: str = "",
        cookies_browser: str | None = None,
        on_change_output: Callable[[], None] | None = None,
        on_change_cookies_browser: Callable[[str | None], None] | None = None,
        on_copy_diagnostics: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)

        self._on_change_cookies_browser = on_change_cookies_browser or _noop
        self._label_to_browser = {label: value for label, value in cookies_browser_choices()}
        self.cookies_browser = normalize_cookies_browser(cookies_browser)

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

        self.cookies_menu = ctk.CTkOptionMenu(
            self,
            values=[label for label, _value in cookies_browser_choices()],
            command=self._on_cookies_browser_change,
            width=160,
        )
        self.cookies_menu.grid(row=1, column=1, sticky="w", padx=(0, 6), pady=(0, 8))
        self.cookies_menu.set(cookies_browser_label(self.cookies_browser))

        self.cookies_hint = ctk.CTkLabel(
            self,
            text="Read cookies from this browser automatically",
            anchor="w",
            font=("Arial", 11),
            text_color="#94a3b8",
        )
        self.cookies_hint.grid(row=1, column=2, sticky="w", padx=(0, 12), pady=(0, 8))

        self.diagnostics_button = ctk.CTkButton(
            self,
            text="Copy diagnostics",
            width=150,
            command=on_copy_diagnostics or _noop,
        )
        self.diagnostics_button.grid(row=0, column=3, rowspan=2, padx=(0, 12), pady=8, sticky="e")

    def set_output_path(self, path: str) -> None:
        self.output_value.configure(text=path or "Not set")

    def set_cookies_browser(self, browser: str | None) -> None:
        self.cookies_browser = normalize_cookies_browser(browser)
        label = cookies_browser_label(self.cookies_browser)
        if self.cookies_menu.get() != label:
            self.cookies_menu.set(label)

    def _on_cookies_browser_change(self, label: str) -> None:
        browser = self._label_to_browser.get(label)
        if label == AUTO_COOKIES_BROWSER_LABEL:
            browser = None
        self.cookies_browser = normalize_cookies_browser(browser)
        self._on_change_cookies_browser(self.cookies_browser)
