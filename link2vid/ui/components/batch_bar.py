"""Selection and batch-action controls for fetched entries."""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk


class BatchBar(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        on_select_all: Callable[[], None],
        on_select_none: Callable[[], None],
        on_download_selected: Callable[[], None],
        on_transcript_selected: Callable[[], None],
        on_cancel: Callable[[], None],
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.running = False
        self.grid_columnconfigure(1, weight=1)

        selection = ctk.CTkFrame(self, fg_color="transparent")
        selection.grid(row=0, column=0, padx=10, pady=8, sticky="w")
        self.selection_label = ctk.CTkLabel(selection, text="0 of 0 selected")
        self.selection_label.pack(side="left", padx=(0, 8))
        self.select_all_button = ctk.CTkButton(selection, text="Select all", width=82, command=on_select_all)
        self.select_all_button.pack(side="left", padx=3)
        self.select_none_button = ctk.CTkButton(selection, text="Select none", width=88, command=on_select_none)
        self.select_none_button.pack(side="left", padx=3)

        self.status_label = ctk.CTkLabel(self, text="", anchor="w")
        self.status_label.grid(row=0, column=1, padx=10, pady=8, sticky="ew")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=0, column=2, padx=10, pady=8, sticky="e")
        self.download_button = ctk.CTkButton(actions, text="Download selected", width=130, command=on_download_selected)
        self.download_button.pack(side="left", padx=3)
        self.transcript_button = ctk.CTkButton(actions, text="Transcript selected", width=135, command=on_transcript_selected)
        self.transcript_button.pack(side="left", padx=3)
        self.stop_button = ctk.CTkButton(actions, text="Stop", width=72, command=on_cancel, fg_color="#b91c1c", hover_color="#991b1b")

    def set_selection_summary(self, selected: int, total: int) -> None:
        self.selection_label.configure(text=f"{selected} of {total} selected")

    def set_running(self, running: bool) -> None:
        self.running = bool(running)
        if self.running:
            self.download_button.pack_forget()
            self.transcript_button.pack_forget()
            self.stop_button.pack(side="left", padx=3)
        else:
            self.stop_button.pack_forget()
            self.download_button.pack(side="left", padx=3)
            self.transcript_button.pack(side="left", padx=3)

    def set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled and not self.running else "disabled"
        for button in (
            self.select_all_button,
            self.select_none_button,
            self.download_button,
            self.transcript_button,
        ):
            button.configure(state=state)
        self.stop_button.configure(state="normal" if self.running else "disabled")
