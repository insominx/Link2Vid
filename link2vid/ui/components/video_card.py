"""Video card component."""

from __future__ import annotations

from typing import Callable, Sequence

import customtkinter as ctk

FormatOption = tuple[str, str]
OnDownload = Callable[["VideoCard", str], None]
OnTranscript = Callable[["VideoCard", object | None], None]
OnWhisper = Callable[["VideoCard", str], None]

STATUS_COLORS = {
    "ready": ("#6b7280", "#9ca3af"),
    "downloading": ("#1d4ed8", "#4ea5ff"),
    "complete": ("#15803d", "#45d483"),
    "failed": ("#b91c1c", "#ff6b6b"),
}

_DEFAULT_WHISPER_MODEL = "small"
_WHISPER_UNAVAILABLE_LABEL = "—"


def _default_format_options() -> list[FormatOption]:
    return [
        ("Best (A+V)", "bestvideo+bestaudio/best"),
        ("Best video", "bestvideo"),
        ("Best audio", "bestaudio"),
    ]


class VideoCard(ctk.CTkFrame):
    def __init__(
        self,
        master,
        title: str,
        metadata: str = "",
        format_options: Sequence[object] | None = None,
        transcript_options: Sequence[object] | None = None,
        whisper_models: Sequence[str] | None = None,
        on_download: OnDownload | None = None,
        on_transcript: OnTranscript | None = None,
        on_whisper: OnWhisper | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.on_download = on_download
        self.on_transcript = on_transcript
        self.on_whisper = on_whisper
        self.title_text = title
        self.selected_format = ""
        self.selected_transcript = None
        self.selected_whisper_model = ""
        self.transcript_available = True
        self.whisper_available = False
        self.transcript_picker = None
        self.status_state = "ready"
        self._progress_mode = "determinate"

        self.configure(corner_radius=12)
        self.grid_columnconfigure(1, weight=1)

        self.thumbnail_frame = ctk.CTkFrame(self, width=120, height=72, corner_radius=8)
        self.thumbnail_frame.grid(row=0, column=0, rowspan=3, padx=12, pady=12, sticky="w")
        self.thumbnail_frame.grid_propagate(False)
        self.thumbnail_label = ctk.CTkLabel(self.thumbnail_frame, text="Thumbnail")
        self.thumbnail_label.place(relx=0.5, rely=0.5, anchor="center")

        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(row=0, column=1, rowspan=3, sticky="nsew", padx=(0, 12), pady=12)
        info_frame.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(
            info_frame,
            text=title,
            font=("Arial", 16, "bold"),
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        self.meta_label = ctk.CTkLabel(
            info_frame,
            text=metadata,
            font=("Arial", 12),
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.meta_label.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.status_label = ctk.CTkLabel(info_frame, text="Ready", font=("Arial", 11), anchor="w")
        self.status_label.grid(row=2, column=0, sticky="w", pady=(6, 0))

        progress_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
        progress_frame.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        progress_frame.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(progress_frame, height=6, mode="determinate")
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.progress_bar.set(0)

        self.progress_label = ctk.CTkLabel(progress_frame, text="0%", font=("Arial", 10), anchor="e")
        self.progress_label.grid(row=0, column=1, sticky="e")

        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.grid(row=0, column=2, rowspan=3, padx=(0, 12), pady=12, sticky="e")
        action_frame.grid_columnconfigure(0, weight=1)

        self.format_options = self._normalize_format_options(format_options)
        labels = [label for label, _value in self.format_options]
        self.format_map = {label: value for label, value in self.format_options}

        self.format_menu = ctk.CTkOptionMenu(action_frame, values=labels, command=self._on_format_change)
        self.format_menu.grid(row=0, column=0, sticky="ew")
        if labels:
            self.format_menu.set(labels[0])
            self.selected_format = self.format_map[labels[0]]

        self.transcript_options, self.transcript_available = self._normalize_transcript_options(transcript_options)
        self.transcript_map = {label: value for label, value in self.transcript_options}
        self.transcript_button_label = ctk.CTkButton(
            action_frame,
            text=self.transcript_options[0][0],
            command=self._open_transcript_picker,
            fg_color="#1f2937",
            hover_color="#374151",
        )
        self.transcript_button_label.grid(row=1, column=0, pady=(8, 0), sticky="ew")
        self.selected_transcript = self.transcript_options[0][1]

        self.download_button = ctk.CTkButton(action_frame, text="Download", command=self._handle_download)
        self.download_button.grid(row=2, column=0, pady=(8, 0), sticky="ew")

        self.transcript_button = ctk.CTkButton(
            action_frame,
            text="Transcript",
            command=self._handle_transcript,
            fg_color="#334155",
            hover_color="#475569",
        )
        self.transcript_button.grid(row=3, column=0, pady=(8, 0), sticky="ew")

        self.whisper_models = self._normalize_whisper_models(whisper_models)
        self.whisper_available = bool(self.whisper_models)
        whisper_values = list(self.whisper_models) if self.whisper_models else [_WHISPER_UNAVAILABLE_LABEL]
        self.whisper_model_menu = ctk.CTkOptionMenu(
            action_frame,
            values=whisper_values,
            command=self._on_whisper_model_change,
        )
        self.whisper_model_menu.grid(row=4, column=0, pady=(8, 0), sticky="ew")
        if self.whisper_models:
            initial = self._default_whisper_model(self.whisper_models)
            self.whisper_model_menu.set(initial)
            self.selected_whisper_model = initial
        else:
            self.whisper_model_menu.set(_WHISPER_UNAVAILABLE_LABEL)
            self.selected_whisper_model = ""

        self.whisper_button = ctk.CTkButton(
            action_frame,
            text="Whisper",
            command=self._handle_whisper,
            fg_color="#0f766e",
            hover_color="#0d9488",
        )
        self.whisper_button.grid(row=5, column=0, pady=(8, 0), sticky="ew")

        self.set_status("Ready", state="ready")
        self.set_actions_enabled(True)

    def _normalize_format_options(self, options: Sequence[object] | None) -> list[FormatOption]:
        if not options:
            return _default_format_options()
        normalized: list[FormatOption] = []
        for option in options:
            if isinstance(option, dict):
                label = option.get("label") or option.get("format") or "Format"
                value = option.get("format") or label
            elif isinstance(option, (list, tuple)) and len(option) >= 2:
                label, value = option[0], option[1]
            else:
                label = str(option)
                value = label
            normalized.append((str(label), str(value)))
        return normalized

    def _normalize_transcript_options(self, options: Sequence[object] | None) -> tuple[list[tuple[str, object | None]], bool]:
        if options is None:
            return [("Auto-select transcript", None)], True
        if not options:
            return [("No transcript tracks", None)], False

        normalized: list[tuple[str, object | None]] = [("Auto-select transcript", None)]
        for option in options:
            if isinstance(option, dict):
                label = option.get("label") or option.get("language") or "Transcript"
                value = option
            elif isinstance(option, (list, tuple)) and len(option) >= 2:
                label, value = option[0], option[1]
            else:
                label = getattr(option, "label", None) or str(option)
                value = option
            normalized.append((str(label), value))
        normalized[1:] = sorted(normalized[1:], key=lambda item: item[0].lower())
        return normalized, True

    @staticmethod
    def _normalize_whisper_models(models: Sequence[str] | None) -> list[str]:
        if not models:
            return []
        # Exact upstream aliases only — never remap to quality presets.
        return [str(alias) for alias in models]

    @staticmethod
    def _default_whisper_model(aliases: Sequence[str]) -> str:
        if _DEFAULT_WHISPER_MODEL in aliases:
            return _DEFAULT_WHISPER_MODEL
        return aliases[0]

    def _on_format_change(self, value: str) -> None:
        if value in self.format_map:
            self.selected_format = self.format_map[value]

    def _on_transcript_change(self, value: str) -> None:
        if value in self.transcript_map:
            self.selected_transcript = self.transcript_map[value]
            self.transcript_button_label.configure(text=value)

    def _on_whisper_model_change(self, value: str) -> None:
        if value in self.whisper_models:
            self.selected_whisper_model = value

    def _open_transcript_picker(self) -> None:
        if not self.transcript_available:
            return
        if self.transcript_picker is not None and self.transcript_picker.winfo_exists():
            self.transcript_picker.focus()
            return

        top = ctk.CTkToplevel(self)
        top.title("Select transcript language")
        top.geometry("460x560")
        top.minsize(360, 420)
        top.transient(self.winfo_toplevel())
        self.transcript_picker = top

        container = ctk.CTkFrame(top)
        container.pack(fill="both", expand=True, padx=12, pady=12)
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)

        search = ctk.CTkEntry(container, placeholder_text="Search language")
        search.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        list_frame = ctk.CTkScrollableFrame(container)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.grid_columnconfigure(0, weight=1)

        def select(label: str) -> None:
            self._on_transcript_change(label)
            top.destroy()

        visible_labels: list[str] = []

        def render(filter_text: str = "") -> None:
            visible_labels.clear()
            for child in list_frame.winfo_children():
                child.destroy()
            lowered = filter_text.strip().lower()
            if lowered:
                starts = [
                    (label, value)
                    for label, value in self.transcript_options
                    if label.lower().startswith(lowered)
                ]
                contains = [
                    (label, value)
                    for label, value in self.transcript_options
                    if lowered in label.lower() and not label.lower().startswith(lowered)
                ]
                rows = [*starts, *contains]
            else:
                rows = list(self.transcript_options)
            visible_labels.extend(label for label, _value in rows)
            if not rows:
                ctk.CTkLabel(list_frame, text="No matching languages", anchor="w").grid(
                    row=0,
                    column=0,
                    sticky="ew",
                    padx=4,
                    pady=4,
                )
                return
            for index, (label, _value) in enumerate(rows):
                button = ctk.CTkButton(
                    list_frame,
                    text=label,
                    anchor="w",
                    command=lambda selected_label=label: select(selected_label),
                    fg_color="#1f2937" if self.transcript_map[label] == self.selected_transcript else "#334155",
                    hover_color="#475569",
                )
                button.grid(row=index, column=0, sticky="ew", padx=4, pady=3)

        def on_search(_event=None) -> None:
            render(search.get())

        search.bind("<KeyRelease>", on_search)
        top.bind("<Escape>", lambda _event: top.destroy())
        top.bind("<Return>", lambda _event: select(visible_labels[0]) if visible_labels else None)
        render()
        search.focus_set()

    def _handle_download(self) -> None:
        if self.on_download:
            self.on_download(self, self.selected_format)

    def _handle_transcript(self) -> None:
        if self.on_transcript:
            self.on_transcript(self, self.selected_transcript)

    def _handle_whisper(self) -> None:
        if self.on_whisper and self.whisper_available and self.selected_whisper_model:
            self.on_whisper(self, self.selected_whisper_model)

    def set_actions_enabled(self, enabled: bool) -> None:
        download_state = "normal" if enabled and self.on_download else "disabled"
        transcript_state = "normal" if enabled and self.on_transcript and self.transcript_available else "disabled"
        transcript_picker_state = "normal" if enabled and self.transcript_available else "disabled"
        whisper_state = "normal" if enabled and self.on_whisper and self.whisper_available else "disabled"
        whisper_menu_state = "normal" if enabled and self.whisper_available else "disabled"
        self.download_button.configure(state=download_state)
        self.transcript_button.configure(state=transcript_state)
        self.transcript_button_label.configure(state=transcript_picker_state)
        self.whisper_button.configure(state=whisper_state)
        self.whisper_model_menu.configure(state=whisper_menu_state)

    def set_title(self, title: str) -> None:
        self.title_text = title
        self.title_label.configure(text=title)

    def set_metadata(self, metadata: str) -> None:
        self.meta_label.configure(text=metadata)

    def _infer_state(self, status: str) -> str:
        text = status.lower()
        if "download" in text:
            return "downloading"
        if "complete" in text or "done" in text:
            return "complete"
        if "fail" in text or "error" in text:
            return "failed"
        if "ready" in text:
            return "ready"
        return "ready"

    def set_status(self, status: str, state: str | None = None) -> None:
        self.status_label.configure(text=status)
        resolved_state = (state or self._infer_state(status)).lower()
        self.status_state = resolved_state
        color = STATUS_COLORS.get(resolved_state)
        if color:
            self.status_label.configure(text_color=color)

    def _stop_progress_animation(self) -> None:
        self.progress_bar.stop()

    def set_progress_mode(self, mode: str) -> None:
        """Switch between determinate and indeterminate card progress.

        Indeterminate uses CTk start/stop animation and never shows a fake percent.
        """
        normalized = str(mode).strip().lower()
        if normalized not in ("determinate", "indeterminate"):
            raise ValueError(f"Unsupported progress mode: {mode!r}")

        self._stop_progress_animation()
        self._progress_mode = normalized
        self.progress_bar.configure(mode=normalized)
        if normalized == "indeterminate":
            self.progress_label.configure(text="")
            self.progress_bar.start()
        else:
            # Preserve the current determinate value; callers use set_progress/reset.
            percent = int(round(max(0.0, min(1.0, float(self.progress_bar.get()))) * 100))
            self.progress_label.configure(text=f"{percent}%")

    def set_progress(self, value: float) -> None:
        """Set determinate progress. Existing download callers remain unchanged."""
        if self._progress_mode != "determinate":
            self.set_progress_mode("determinate")
        clamped = max(0.0, min(1.0, value))
        self.progress_bar.set(clamped)
        percent = int(round(clamped * 100))
        self.progress_label.configure(text=f"{percent}%")

    def reset_progress(self) -> None:
        """Stop any animation and return to determinate 0%."""
        self._stop_progress_animation()
        self._progress_mode = "determinate"
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(0)
        self.progress_label.configure(text="0%")

    def get_progress_mode(self) -> str:
        return self._progress_mode

    def get_selected_whisper_model(self) -> str:
        return self.selected_whisper_model

    def set_thumbnail_image(self, image) -> None:
        self.thumbnail_label.configure(image=image, text="")
        self.thumbnail_label.image = image

    def set_thumbnail_text(self, text: str) -> None:
        self.thumbnail_label.configure(text=text, image=None)

    def get_title(self) -> str:
        return self.title_text
