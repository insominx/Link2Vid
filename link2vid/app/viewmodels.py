"""Toolkit-neutral view models published to the UI.

Each model replaces a former CustomTkinter widget. The controller mutates them
only on its own dispatcher thread; every mutation publishes a plain-JSON event
through ``emit(event_type, payload)`` so any frontend can render the state.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Sequence

Emit = Callable[[str, dict], None]

DEFAULT_WHISPER_MODEL = "large-v3"

_PROGRESS_MODES = ("determinate", "indeterminate")
_ERROR_LINE = re.compile(r"^\[[^\]]+\]\s+\w*(Error|Exception)\b")


def _noop_emit(_event: str, _payload: dict) -> None:
    return None


def _default_format_options() -> list[tuple[str, str]]:
    return [
        ("Best (A+V)", "bestvideo+bestaudio/best"),
        ("Best video", "bestvideo"),
        ("Audio (MP3)", "bestaudio"),
    ]


def normalize_format_options(options: Sequence[object] | None) -> list[tuple[str, str]]:
    if not options:
        return _default_format_options()
    normalized: list[tuple[str, str]] = []
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


def normalize_transcript_options(
    options: Sequence[object] | None,
) -> tuple[list[tuple[str, object | None]], bool]:
    """Return ``(options, available)``: Auto-select, then subtitles, then automatic captions.

    ``None`` means the entry carries no caption metadata, so Auto-select stays
    available; an empty sequence means the provider reported no tracks.
    """
    if options is None:
        return [("Auto-select", None)], True
    if not options:
        return [("No caption tracks", None)], False
    normalized: list[tuple[str, object | None]] = []
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
    # Real subtitle tracks first, machine-translated captions after.
    normalized.sort(
        key=lambda item: (
            str(getattr(item[1], "source", "") or "").startswith("automatic"),
            item[0].lower(),
        )
    )
    return [("Auto-select", None), *normalized], True


def default_whisper_model(aliases: Sequence[str]) -> str:
    if DEFAULT_WHISPER_MODEL in aliases:
        return DEFAULT_WHISPER_MODEL
    return aliases[0] if aliases else ""


def infer_card_state(status: str) -> str:
    text = status.lower()
    if "download" in text:
        return "downloading"
    if "complete" in text or "done" in text or "saved" in text:
        return "complete"
    if "fail" in text or "error" in text:
        return "failed"
    return "ready"


def log_level(message: str) -> str:
    if _ERROR_LINE.match(message):
        return "error"
    lowered = message.lower()
    if "warning" in lowered or lowered.startswith("[warn"):
        return "warn"
    return "info"


class CardModel:
    """State for one fetched entry's card.

    Mirrors the old ``VideoCard`` widget API (``set_status``, ``set_progress``,
    ``set_actions_enabled`` ...) so controller orchestration stays unchanged.
    Identity, not equality, keys every controller map.
    """

    def __init__(
        self,
        *,
        index: int,
        generation: int = 0,
        title: str,
        details: dict | None = None,
        format_options: Sequence[object] | None = None,
        transcript_options: Sequence[object] | None = None,
        whisper_models: Sequence[str] | None = None,
        on_download: Callable[["CardModel", str], None] | None = None,
        on_transcript: Callable[["CardModel", object | None], None] | None = None,
        on_whisper: Callable[["CardModel", str], None] | None = None,
        selected: bool = True,
        on_selection_change: Callable[["CardModel", bool], None] | None = None,
        on_format_change: Callable[["CardModel", str], None] | None = None,
        on_transcript_change: Callable[["CardModel", object | None], None] | None = None,
        emit: Emit | None = None,
    ) -> None:
        self.index = index
        self.generation = generation
        self.title_text = title
        self.details = dict(details or {})
        self.on_download = on_download
        self.on_transcript = on_transcript
        self.on_whisper = on_whisper
        self.on_selection_change = on_selection_change
        self.on_format_change = on_format_change
        self.on_transcript_change = on_transcript_change
        self._emit = emit or _noop_emit
        self._alive = True

        self.format_options = normalize_format_options(format_options)
        self.selected_format = self.format_options[0][1] if self.format_options else ""
        self.transcript_options, self.transcript_available = normalize_transcript_options(
            transcript_options
        )
        self.transcript_choice = 0
        self.selected_transcript = self.transcript_options[0][1]
        self.whisper_models = [str(alias) for alias in (whisper_models or [])]
        self.whisper_available = bool(self.whisper_models)
        self.selected_whisper_model = default_whisper_model(self.whisper_models)

        self.selected = bool(selected)
        self.status = "Ready"
        self.status_state = "ready"
        self.progress = 0.0
        self._progress_mode = "determinate"
        self.actions_enabled = True
        self.outputs: list[str] = []
        if not self.transcript_available:
            self.status = "No caption tracks"

    # ── identity / lifecycle ────────────────────────────────
    @property
    def key(self) -> str:
        return f"{self.generation}:{self.index}"

    def is_alive(self) -> bool:
        return self._alive

    def destroy(self) -> None:
        self._alive = False

    # ── serialization ───────────────────────────────────────
    def describe(self) -> dict:
        """Full static + dynamic payload used when the card first renders."""
        return {
            "key": self.key,
            "index": self.index,
            "generation": self.generation,
            "title": self.title_text,
            **self.details,
            "transcripts": [label for label, _value in self.transcript_options],
            "transcript_available": self.transcript_available,
            "whisper_available": self.whisper_available,
            **self.state(),
        }

    def state(self) -> dict:
        return {
            "key": self.key,
            "selected": self.selected,
            "format": self.selected_format,
            "transcript_choice": self.transcript_choice,
            "whisper_model": self.selected_whisper_model,
            "status": self.status,
            "state": self.status_state,
            "progress": round(self.progress, 4),
            "mode": self._progress_mode,
            "enabled": self.actions_enabled,
            "can_download": self.actions_enabled and self.on_download is not None,
            "can_transcript": (
                self.actions_enabled
                and self.on_transcript is not None
                and self.transcript_available
            ),
            "can_whisper": (
                self.actions_enabled
                and self.on_whisper is not None
                and self.whisper_available
            ),
            "outputs": list(self.outputs),
        }

    def _publish(self) -> None:
        if self._alive:
            self._emit("card", self.state())

    # ── user intents (from the frontend) ────────────────────
    def choose_format(self, format_id: str) -> None:
        values = [value for _label, value in self.format_options]
        if format_id not in values:
            return
        self.selected_format = format_id
        if self.on_format_change:
            self.on_format_change(self, format_id)
        self._publish()

    def choose_transcript(self, choice: int) -> None:
        if not self.transcript_available:
            return
        try:
            position = int(choice)
        except (TypeError, ValueError):
            return
        if position < 0 or position >= len(self.transcript_options):
            return
        self.transcript_choice = position
        self.selected_transcript = self.transcript_options[position][1]
        if self.on_transcript_change:
            self.on_transcript_change(self, self.selected_transcript)
        self._publish()

    def choose_whisper_model(self, model: str) -> None:
        if model in self.whisper_models:
            self.selected_whisper_model = model
            self._publish()

    def request_download(self) -> None:
        if self.on_download:
            self.on_download(self, self.selected_format)

    def request_transcript(self) -> None:
        if self.on_transcript and self.transcript_available:
            self.on_transcript(self, self.selected_transcript)

    def request_whisper(self) -> None:
        if self.on_whisper and self.whisper_available and self.selected_whisper_model:
            self.on_whisper(self, self.selected_whisper_model)

    def toggle_selected(self, value: bool) -> None:
        self.selected = bool(value)
        if self.on_selection_change:
            self.on_selection_change(self, self.selected)
        self._publish()

    # ── controller-facing widget API ────────────────────────
    def is_selected(self) -> bool:
        return self.selected

    def set_selected(self, value: bool, *, notify: bool = False) -> None:
        changed = self.selected != bool(value)
        self.selected = bool(value)
        if notify and self.on_selection_change:
            self.on_selection_change(self, self.selected)
        if changed:
            self._publish()

    def set_actions_enabled(self, enabled: bool) -> None:
        self.actions_enabled = bool(enabled)
        self._publish()

    def set_status(self, status: str, state: str | None = None) -> None:
        self.status = status
        self.status_state = (state or infer_card_state(status)).lower()
        if self.status_state == "downloading":
            self.outputs = []
        self._publish()

    def set_progress_mode(self, mode: str) -> None:
        normalized = str(mode).strip().lower()
        if normalized not in _PROGRESS_MODES:
            raise ValueError(f"Unsupported progress mode: {mode!r}")
        self._progress_mode = normalized
        self._publish()

    def set_progress(self, value: float) -> None:
        self._progress_mode = "determinate"
        self.progress = max(0.0, min(1.0, float(value)))
        self._publish()

    def reset_progress(self) -> None:
        self._progress_mode = "determinate"
        self.progress = 0.0
        self._publish()

    def get_progress_mode(self) -> str:
        return self._progress_mode

    def set_outputs(self, paths: Sequence[str]) -> None:
        self.outputs = [str(path) for path in paths if path]
        self._publish()

    def get_selected_whisper_model(self) -> str:
        return self.selected_whisper_model

    def get_title(self) -> str:
        return self.title_text


class BatchBarModel:
    """Selection summary and batch-run controls for multi-entry results."""

    def __init__(self, emit: Emit | None = None) -> None:
        self._emit = emit or _noop_emit
        self.visible = False
        self.running = False
        self.enabled = False
        self.status = ""
        self.selected = 0
        self.total = 0

    def state(self) -> dict:
        return {
            "visible": self.visible,
            "running": self.running,
            "enabled": self.enabled and not self.running,
            "status": self.status,
            "selected": self.selected,
            "total": self.total,
        }

    def _publish(self) -> None:
        self._emit("batch", self.state())

    def show(self) -> None:
        self.visible = True
        self._publish()

    def hide(self) -> None:
        self.visible = False
        self.status = ""
        self._publish()

    def set_selection_summary(self, selected: int, total: int) -> None:
        self.selected, self.total = int(selected), int(total)
        self._publish()

    def set_running(self, running: bool) -> None:
        self.running = bool(running)
        self._publish()

    def set_status(self, text: str) -> None:
        self.status = text
        self._publish()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._publish()


class FooterModel:
    """Output folder and cookie-source settings shown in the status bar."""

    def __init__(
        self,
        *,
        output_path: str = "",
        cookies_browser: str | None = None,
        cookies_path: str | None = None,
        emit: Emit | None = None,
    ) -> None:
        self._emit = emit or _noop_emit
        self.output_path = output_path
        self.cookies_browser = cookies_browser
        self.cookies_path = cookies_path

    def state(self) -> dict:
        return {
            "output_path": self.output_path,
            "cookies_browser": self.cookies_browser,
            "cookies_path": self.cookies_path,
        }

    def _publish(self) -> None:
        self._emit("settings", self.state())

    def set_output_path(self, path: str) -> None:
        self.output_path = path
        self._publish()

    def set_cookies_browser(self, browser: str | None) -> None:
        self.cookies_browser = browser
        self._publish()

    def set_cookies_path(self, path: str | None) -> None:
        self.cookies_path = path
        self._publish()


class UrlModel:
    """The address field. The frontend owns typing; Python mirrors the text."""

    def __init__(self, emit: Emit | None = None) -> None:
        self._emit = emit or _noop_emit
        self.value = ""

    def get(self) -> str:
        return self.value

    def set(self, value: str, *, publish: bool = True) -> None:
        self.value = str(value or "")
        if publish:
            self._emit("url", {"value": self.value})


class LogModel:
    """Activity log lines with a coarse level for highlighting."""

    def __init__(self, emit: Emit | None = None) -> None:
        self._emit = emit or _noop_emit

    def append(self, message: str) -> None:
        self._emit(
            "log",
            {
                "time": time.strftime("%H:%M:%S", time.localtime()),
                "level": log_level(message),
                "text": message,
            },
        )


__all__ = [
    "BatchBarModel",
    "CardModel",
    "DEFAULT_WHISPER_MODEL",
    "FooterModel",
    "LogModel",
    "UrlModel",
    "default_whisper_model",
    "infer_card_state",
    "log_level",
    "normalize_format_options",
    "normalize_transcript_options",
]
