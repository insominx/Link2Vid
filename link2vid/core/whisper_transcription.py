"""Faster Whisper transcription contracts, service, and paired output writer.

This module intentionally does not import Faster Whisper or CTranslate2 at
import time. Dependency probing stays lazy so a native import failure can
disable only Whisper without breaking the rest of core.
"""

from __future__ import annotations

import math
import os
import re
import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .helpers import sanitize_filename
from .runtime import model_cache_dir

DEFAULT_WHISPER_MODEL = "large-v3"
_MISSING_LANGUAGE = "und"
_MAX_PAIR_SUFFIX = 10_000

ModelFactory = Callable[[str, str, str, str], Any]
CudaDeviceCountFn = Callable[[], int]
AvailableModelsFn = Callable[[], Sequence[str]]
CacheDirFn = Callable[[], Path]
PhaseCallback = Callable[["WhisperPhase"], None]


class WhisperPhase(str, Enum):
    PREPARING_AUDIO = "preparing_audio"
    DOWNLOADING_AUDIO = "downloading_audio"
    WAITING_FOR_SERVICE = "waiting_for_service"
    LOADING_MODEL = "loading_model"
    FALLING_BACK_CPU = "falling_back_cpu"
    TRANSCRIBING = "transcribing"
    WRITING = "writing"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class VerifiedMedia:
    path: Path
    size: int
    modified_at: float

    def is_current(self) -> bool:
        """Return True when the path is still the exact verified media file."""
        try:
            if not self.path.is_file():
                return False
            stat = self.path.stat()
        except OSError:
            return False
        if stat.st_size <= 0:
            return False
        return stat.st_size == self.size and stat.st_mtime == self.modified_at


@dataclass(frozen=True)
class TranscriptionRequest:
    request_id: str
    source_url: str
    title: str
    output_dir: Path
    model_name: str
    verified_media: VerifiedMedia | None


@dataclass(frozen=True)
class TranscriptionResult:
    text_path: Path
    srt_path: Path
    language: str
    segment_count: int
    model_name: str
    effective_device: str
    fallback_used: bool


@dataclass(frozen=True)
class WhisperRunSnapshot:
    request_id: str
    phase: WhisperPhase
    model_name: str
    cache_root: str
    effective_device: str | None
    fallback_used: bool
    source_kind: str | None
    error_reason: str | None
    updated_at: float


class GpuInitializationUnavailable(Exception):
    """Typed internal reason that permits one CPU model-construction attempt."""


class InvalidWhisperModelError(ValueError):
    """Raised when a model name is not an exact Faster Whisper alias."""


class NoSpeechError(RuntimeError):
    """Raised when transcription yields no accepted nonblank segments."""


class WhisperServiceReentrancyError(RuntimeError):
    """Raised when a phase callback re-enters the same service on its thread."""


@dataclass(frozen=True)
class _LoadedSlot:
    model_name: str
    device: str
    compute_type: str
    model: Any


@dataclass(frozen=True)
class _ReservedPair:
    text_path: Path
    srt_path: Path
    placeholders: tuple[Path, Path]


def _import_available_models() -> Callable[[], Sequence[str]]:
    from faster_whisper import available_models

    return available_models


def _import_whisper_model() -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel


def _import_cuda_device_count() -> CudaDeviceCountFn:
    import ctranslate2

    return ctranslate2.get_cuda_device_count


def available_models() -> list[str]:
    """Return exact upstream Faster Whisper model aliases (lazy import)."""
    return list(_import_available_models()())


def validate_model_name(model_name: str, models: Sequence[str] | None = None) -> str:
    """Require an exact member of the upstream alias list; reject paths/IDs."""
    aliases = list(models) if models is not None else available_models()
    if model_name not in aliases:
        raise InvalidWhisperModelError(
            f"Unknown Whisper model {model_name!r}; choose an exact alias from available_models()"
        )
    return model_name


def default_model_name(models: Sequence[str] | None = None) -> str:
    """Return ``large-v3`` when present; otherwise the first upstream alias."""
    aliases = list(models) if models is not None else available_models()
    if not aliases:
        raise InvalidWhisperModelError("Faster Whisper reported no available models")
    if DEFAULT_WHISPER_MODEL in aliases:
        return DEFAULT_WHISPER_MODEL
    return aliases[0]


def sanitize_language_code(language: str | None) -> str:
    """Sanitize a detected language code for filename use; default to ``und``."""
    if language is None:
        return _MISSING_LANGUAGE
    raw = str(language).strip().lower()
    if not raw:
        return _MISSING_LANGUAGE
    cleaned = sanitize_filename(raw, max_length=32)
    cleaned = re.sub(r"[^a-z0-9_-]+", "", cleaned).strip("_-")
    return cleaned or _MISSING_LANGUAGE


def format_srt_timestamp(seconds: float) -> str:
    """Format a non-negative timestamp as ``HH:MM:SS,mmm`` for SRT cues."""
    value = float(seconds)
    _require_finite_timestamp(value)
    if value < 0:
        value = 0.0
    total_ms = int(round(value * 1000.0 + 1e-9))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _require_finite_timestamp(value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"SRT timestamp must be finite, got {value!r}")


def normalize_segment_text(text: Any) -> str:
    """Return stripped segment text; blank results are rejected by callers."""
    if text is None:
        return ""
    return str(text).strip()


def _default_model_factory(
    model_name: str,
    device: str,
    compute_type: str,
    download_root: str,
) -> Any:
    WhisperModel = _import_whisper_model()
    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        download_root=download_root,
    )


def _default_cuda_device_count() -> int:
    return int(_import_cuda_device_count()())


def _remove_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _exclusive_create(path: Path) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(str(path), flags)
    try:
        os.close(fd)
    except BaseException:
        # A failed close is ambiguous: the descriptor number may already have
        # been released and reused by another thread. Never retry it. Removing
        # the exclusively created path is safe and cannot mask the close error.
        _remove_path(path)
        raise


def reserve_output_pair(output_dir: Path, title: str, language: str) -> _ReservedPair:
    """Reserve a collision-safe ``.txt``/``.srt`` pair with exclusive placeholders."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(title)
    lang = sanitize_language_code(language)
    base_stem = f"{safe_title}.whisper.{lang}"

    for index in range(1, _MAX_PAIR_SUFFIX + 1):
        suffix = "" if index == 1 else f" ({index})"
        text_path = output_dir / f"{base_stem}{suffix}.txt"
        srt_path = output_dir / f"{base_stem}{suffix}.srt"
        try:
            _exclusive_create(text_path)
        except FileExistsError:
            continue
        try:
            _exclusive_create(srt_path)
        except FileExistsError:
            _remove_path(text_path)
            continue
        except Exception:
            _remove_path(text_path)
            raise
        return _ReservedPair(
            text_path=text_path,
            srt_path=srt_path,
            placeholders=(text_path, srt_path),
        )
    raise RuntimeError(f"Unable to reserve Whisper output pair under {output_dir}")


def _cleanup_owned(
    *,
    placeholders: Iterable[Path] = (),
    temps: Iterable[Path] = (),
    published: Iterable[Path] = (),
) -> None:
    for path in temps:
        _remove_path(Path(path))
    for path in placeholders:
        _remove_path(Path(path))
    for path in published:
        _remove_path(Path(path))


def write_paired_transcripts(
    *,
    output_dir: Path,
    title: str,
    language: str,
    segments: Iterable[Any],
) -> tuple[Path, Path, int]:
    """Stream accepted segments to temporary writers, then publish a verified pair.

    User media is never touched. Only action-owned placeholders, temporary writers,
    and a partially published pair member are removed on failure.
    """
    reserved = reserve_output_pair(output_dir, title, language)
    token = uuid.uuid4().hex
    text_tmp = Path(output_dir) / f".link2vid-whisper-{token}.txt"
    srt_tmp = Path(output_dir) / f".link2vid-whisper-{token}.srt"
    published: list[Path] = []
    text_fh = None
    srt_fh = None
    count = 0

    try:
        text_fh = open(text_tmp, "w", encoding="utf-8", newline="\n")
        srt_fh = open(srt_tmp, "w", encoding="utf-8", newline="\n")

        for segment in segments:
            text = normalize_segment_text(getattr(segment, "text", segment))
            if not text:
                continue
            count += 1
            start_value = getattr(segment, "start", 0.0)
            start = 0.0 if start_value is None else float(start_value)
            end_value = getattr(segment, "end", None)
            end = start if end_value is None else float(end_value)
            _require_finite_timestamp(start)
            _require_finite_timestamp(end)
            if end < start:
                end = start
            text_fh.write(text)
            text_fh.write("\n")
            if count > 1:
                srt_fh.write("\n")
            srt_fh.write(f"{count}\n")
            srt_fh.write(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n")
            srt_fh.write(f"{text}\n")

        text_fh.close()
        text_fh = None
        srt_fh.close()
        srt_fh = None

        if count <= 0:
            raise NoSpeechError("Whisper produced no nonblank speech segments")

        if text_tmp.stat().st_size <= 0 or srt_tmp.stat().st_size <= 0:
            raise RuntimeError("Whisper temporary transcript writers were empty")

        os.replace(str(text_tmp), str(reserved.text_path))
        published.append(reserved.text_path)
        os.replace(str(srt_tmp), str(reserved.srt_path))
        published.append(reserved.srt_path)

        if reserved.text_path.stat().st_size <= 0 or reserved.srt_path.stat().st_size <= 0:
            raise RuntimeError("Published Whisper transcript pair failed non-empty verification")

        return reserved.text_path, reserved.srt_path, count
    except Exception:
        if text_fh is not None:
            try:
                text_fh.close()
            except OSError:
                pass
        if srt_fh is not None:
            try:
                srt_fh.close()
            except OSError:
                pass
        _cleanup_owned(
            placeholders=reserved.placeholders,
            temps=(text_tmp, srt_tmp),
            published=published,
        )
        raise


class WhisperService:
    """UI-independent Faster Whisper lifecycle owner for one app process."""

    def __init__(
        self,
        *,
        model_factory: ModelFactory | None = None,
        cuda_device_count: CudaDeviceCountFn | None = None,
        available_models_fn: AvailableModelsFn | None = None,
        cache_dir_fn: CacheDirFn | None = None,
    ) -> None:
        self._model_factory = model_factory or _default_model_factory
        self._cuda_device_count = cuda_device_count or _default_cuda_device_count
        self._available_models_fn = available_models_fn or available_models
        self._cache_dir_fn = cache_dir_fn or model_cache_dir
        self._lock = threading.Lock()
        self._thread_state = threading.local()
        self._slot: _LoadedSlot | None = None

    @property
    def loaded_slot(self) -> tuple[str, str, str] | None:
        """Return the committed reusable ``(model_name, device, compute_type)`` key."""
        if self._slot is None:
            return None
        return (self._slot.model_name, self._slot.device, self._slot.compute_type)

    def list_available_models(self) -> list[str]:
        """Return a defensive list of exact aliases from the lazy adapter."""
        return list(self._available_models_fn())

    def transcribe(
        self,
        request: TranscriptionRequest,
        source_path: Path | str,
        on_phase: PhaseCallback | None = None,
    ) -> TranscriptionResult:
        """Serialize load/inference, stream segments, and publish a verified pair."""
        if getattr(self._thread_state, "active", False):
            raise WhisperServiceReentrancyError(
                "WhisperService.transcribe cannot be re-entered from a phase callback"
            )

        self._thread_state.active = True
        published: tuple[Path, ...] = ()
        try:
            validate_model_name(request.model_name, models=self.list_available_models())
            source = Path(source_path)
            self._emit(on_phase, WhisperPhase.WAITING_FOR_SERVICE)

            with self._lock:
                model, device, fallback_used = self._ensure_model(request.model_name, on_phase)
                self._emit(on_phase, WhisperPhase.TRANSCRIBING)
                segments_iter, info = model.transcribe(
                    str(source),
                    language=None,
                    task="transcribe",
                    beam_size=5,
                    word_timestamps=False,
                )
                language = sanitize_language_code(getattr(info, "language", None))
                self._emit(on_phase, WhisperPhase.WRITING)
                text_path, srt_path, segment_count = write_paired_transcripts(
                    output_dir=Path(request.output_dir),
                    title=request.title,
                    language=language,
                    segments=segments_iter,
                )
                published = (text_path, srt_path)
                self._emit(on_phase, WhisperPhase.COMPLETE)
                return TranscriptionResult(
                    text_path=text_path,
                    srt_path=srt_path,
                    language=language,
                    segment_count=segment_count,
                    model_name=request.model_name,
                    effective_device=device,
                    fallback_used=fallback_used,
                )
        except Exception:
            _cleanup_owned(published=published)
            raise
        finally:
            self._thread_state.active = False

    def _ensure_model(
        self,
        model_name: str,
        on_phase: PhaseCallback | None,
    ) -> tuple[Any, str, bool]:
        cuda_key = (model_name, "cuda", "float16")
        cpu_key = (model_name, "cpu", "int8")

        if self._slot_matches(cuda_key):
            assert self._slot is not None
            return self._slot.model, "cuda", False

        # Narrow typed boundary: only the CUDA availability API may authorize
        # one CPU/int8 retry. Constructor/download failures propagate as-is.
        try:
            self._require_cuda_available()
        except GpuInitializationUnavailable:
            if self._slot_matches(cpu_key):
                assert self._slot is not None
                return self._slot.model, "cpu", True
            self._emit(on_phase, WhisperPhase.LOADING_MODEL)
            self._emit(on_phase, WhisperPhase.FALLING_BACK_CPU)
            download_root = self._prepare_download_root()
            model = self._model_factory(model_name, "cpu", "int8", download_root)
            self._slot = _LoadedSlot(
                model_name=model_name,
                device="cpu",
                compute_type="int8",
                model=model,
            )
            return model, "cpu", True

        self._emit(on_phase, WhisperPhase.LOADING_MODEL)
        download_root = self._prepare_download_root()
        model = self._model_factory(model_name, "cuda", "float16", download_root)
        self._slot = _LoadedSlot(
            model_name=model_name,
            device="cuda",
            compute_type="float16",
            model=model,
        )
        return model, "cuda", False

    def _prepare_download_root(self) -> str:
        download_root = str(self._cache_dir_fn())
        Path(download_root).mkdir(parents=True, exist_ok=True)
        return download_root

    def _require_cuda_available(self) -> None:
        count = self._cuda_device_count()
        if int(count) <= 0:
            raise GpuInitializationUnavailable("No CUDA devices available")

    def _slot_matches(self, target: tuple[str, str, str]) -> bool:
        if self._slot is None:
            return False
        return (
            self._slot.model_name == target[0]
            and self._slot.device == target[1]
            and self._slot.compute_type == target[2]
        )

    @staticmethod
    def _emit(on_phase: PhaseCallback | None, phase: WhisperPhase) -> None:
        if on_phase is not None:
            on_phase(phase)
