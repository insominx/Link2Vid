"""Collapsible log drawer component."""

from __future__ import annotations

import customtkinter as ctk


class LogDrawer(ctk.CTkFrame):
    def __init__(self, master, collapsed: bool = True, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self.expanded = not collapsed

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))
        header.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(header, text="Logs", font=("Arial", 13, "bold"))
        self.title_label.grid(row=0, column=0, sticky="w")

        self.toggle_button = ctk.CTkButton(header, text="Show logs", width=110, command=self.toggle)
        self.toggle_button.grid(row=0, column=1, sticky="e")

        self.textbox = ctk.CTkTextbox(self, height=140)
        self.textbox.grid(row=1, column=0, sticky="nsew", padx=12, pady=(6, 12))
        self.textbox.configure(state="disabled")

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        if collapsed:
            self.textbox.grid_remove()
        else:
            self.toggle_button.configure(text="Hide logs")

    def toggle(self) -> None:
        if self.expanded:
            self.collapse()
        else:
            self.expand()

    def expand(self) -> None:
        self.expanded = True
        self.textbox.grid()
        self.toggle_button.configure(text="Hide logs")

    def collapse(self) -> None:
        self.expanded = False
        self.textbox.grid_remove()
        self.toggle_button.configure(text="Show logs")

    def append(self, message: str) -> None:
        self.textbox.configure(state="normal")
        self.textbox.insert("end", message + "\n")
        self.textbox.see("end")
        self.textbox.configure(state="disabled")
