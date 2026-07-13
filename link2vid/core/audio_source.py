"""Exact-media reuse and action-owned temporary-audio source leases.

``AudioSourceResolver.resolve`` reuses current ``request.verified_media`` when
filesystem revalidation still passes. Otherwise it downloads ``bestaudio`` into
a unique action-owned temporary workspace via an injected ``DownloadManager``.

Dependency direction: this module imports frozen request/media values from
``whisper_transcription`` and typed cookie errors from ``errors``. It never
imports UI.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import tempfile
import threading
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Optional, Type

from .errors import CookiesRequiredError
from .whisper_transcription import TranscriptionRequest

ProgressHook = Callable[..., Any]

_BESTAUDIO_FORMAT = "bestaudio"
_OUTTMPL_NAME = "%(title)s.%(ext)s"
_WORKSPACE_PREFIX = "link2vid-whisper-audio-"

_PARTIAL_SUFFIXES = (
    ".part",
    ".ytdl",
    ".temp",
    ".tmp",
)
_FRAGMENT_NAME_RE = re.compile(r"\.f\d+\.[^.]+$", re.IGNORECASE)
_PARTIAL_FRAGMENT_NAME_RE = re.compile(
    r"(?:\.part-frag\d+|\.frag\d+)$",
    re.IGNORECASE,
)
_SIDECAR_SUFFIXES = (
    ".info.json",
    ".live_chat.json",
    ".annotations.xml",
    ".description",
    ".metadata",
    ".metadata.json",
    ".meta",
    ".meta.json",
    ".json",
    ".xml",
    ".txt",
    ".nfo",
    ".vtt",
    ".srt",
    ".ass",
    ".ssa",
    ".lrc",
    ".json3",
    ".srv1",
    ".srv2",
    ".srv3",
    ".ttml",
    ".dfxp",
    ".smi",
    ".sami",
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".ico",
    ".svg",
    ".avif",
    ".heic",
)

_MISSING_MANAGER_MESSAGE = (
    "Audio download is unavailable; no download manager was configured for "
    "transcript-only Whisper."
)
_DOWNLOAD_FAILED_MESSAGE = (
    "Audio download failed; no usable audio source was produced."
)
_NO_OUTPUT_MESSAGE = (
    "Audio download reported success, but no usable audio file was found."
)
_OUTSIDE_OUTPUT_MESSAGE = (
    "Audio download reported a path outside the owned temporary workspace."
)
_AMBIGUOUS_OUTPUT_MESSAGE = (
    "Audio download produced multiple usable files; refusing ambiguous source."
)
_UNSAFE_OUTPUT_MESSAGE = (
    "Audio download did not produce a safe, non-empty audio file."
)
_LEASE_FAILED_MESSAGE = (
    "Audio download succeeded, but the temporary source lease could not be created."
)
_GENERIC_ACQUISITION_MESSAGE = (
    "Audio download failed; could not obtain a usable audio source."
)


class SourceKind(str, Enum):
    REUSED = "reused"
    TEMPORARY = "temporary"


class AudioAcquisitionError(RuntimeError):
    """Typed failure when a usable audio source cannot be obtained."""


_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _lstat(path: Path) -> os.stat_result:
    return os.lstat(str(path))


def _is_link_or_reparse(path_stat: os.stat_result) -> bool:
    """Return whether an lstat result identifies a link or Windows reparse point."""
    attributes = int(getattr(path_stat, "st_file_attributes", 0))
    return stat.S_ISLNK(path_stat.st_mode) or bool(
        attributes & _REPARSE_POINT_ATTRIBUTE
    )


def _is_strict_descendant(path: Path, directory: Path) -> bool:
    """Python 3.9-compatible strict containment check for resolved paths."""
    return path != directory and directory in path.parents


def _is_partial_artifact_name(name: str) -> bool:
    lowered = name.lower()
    if any(lowered.endswith(suffix) for suffix in _PARTIAL_SUFFIXES):
        return True
    return bool(
        _FRAGMENT_NAME_RE.search(lowered)
        or _PARTIAL_FRAGMENT_NAME_RE.search(lowered)
    )


def _is_known_sidecar_name(name: str) -> bool:
    """Return whether a filename is a known non-media yt-dlp sidecar."""
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in _SIDECAR_SUFFIXES)


def _remove_owned_workspace(workspace: Path) -> None:
    """Best-effort removal of a pre-lease owned workspace."""
    try:
        shutil.rmtree(str(workspace))
    except FileNotFoundError:
        return
    except OSError:
        shutil.rmtree(str(workspace), ignore_errors=True)


class SourceLease:
    """Context-managed media source with explicit cleanup ownership.

    ``release()`` is idempotent and removes only a non-null owned workspace.
    Reused user media never supplies a workspace, so release is a no-op for it.
    A temporary kind without ``workspace`` also never deletes ``path``.

    V1 supports normal app-created workspaces within one desktop process.
    Static links/reparse points are rejected, but active external replacement
    between filesystem checks is outside this single-process ownership model.
    """

    def __init__(
        self,
        path: Path | str,
        kind: SourceKind,
        workspace: Path | str | None = None,
    ) -> None:
        if not isinstance(kind, SourceKind):
            raise TypeError(f"kind must be SourceKind, got {type(kind)!r}")
        resolved_path = Path(path)
        resolved_workspace = None if workspace is None else Path(workspace)
        if kind is SourceKind.REUSED and resolved_workspace is not None:
            raise ValueError(
                "REUSED SourceLease cannot own a temporary workspace; "
                "pass workspace=None for user media"
            )

        if kind is SourceKind.TEMPORARY and resolved_workspace is not None:
            self._validate_owned_workspace(resolved_workspace)
            resolved_workspace = resolved_workspace.resolve(strict=True)
            resolved_path = resolved_path.resolve(strict=False)
            if not _is_strict_descendant(resolved_path, resolved_workspace):
                raise ValueError(
                    "TEMPORARY SourceLease path must resolve beneath its "
                    f"owned workspace: path={resolved_path}, "
                    f"workspace={resolved_workspace}"
                )

        self.path = resolved_path
        self.kind = kind
        self.workspace = resolved_workspace
        self._released = False
        self._release_lock = threading.Lock()

    def release(self) -> None:
        """Idempotently remove only the validated owned workspace, if any.

        A failed removal leaves the lease unreleased and retryable. Calls are
        serialized so only one caller can inspect or mutate cleanup state.
        """
        with self._release_lock:
            if self._released:
                return
            workspace = self.workspace
            if workspace is None:
                self._released = True
                return

            try:
                workspace_stat = _lstat(workspace)
            except FileNotFoundError:
                self._released = True
                return
            except OSError as exc:
                raise self._cleanup_error(workspace, exc) from exc

            if _is_link_or_reparse(workspace_stat):
                error = OSError(
                    errno.EPERM,
                    "Owned audio workspace is a link or reparse point; "
                    "refusing to follow it",
                    str(workspace),
                )
                raise self._cleanup_error(workspace, error) from error
            if not stat.S_ISDIR(workspace_stat.st_mode):
                error = NotADirectoryError(
                    errno.ENOTDIR,
                    "Owned audio workspace is no longer a directory",
                    str(workspace),
                )
                raise self._cleanup_error(workspace, error) from error

            try:
                shutil.rmtree(
                    str(workspace),
                    onerror=self._readonly_cleanup_callback(workspace),
                )
            except FileNotFoundError as exc:
                try:
                    _lstat(workspace)
                except FileNotFoundError:
                    self._released = True
                    return
                except OSError as verify_exc:
                    raise self._cleanup_error(workspace, verify_exc) from verify_exc
                raise self._cleanup_error(workspace, exc) from exc
            except OSError as exc:
                raise self._cleanup_error(workspace, exc) from exc

            try:
                _lstat(workspace)
            except FileNotFoundError:
                self._released = True
                return
            except OSError as exc:
                raise self._cleanup_error(workspace, exc) from exc

            error = OSError(
                errno.EIO,
                "Owned audio workspace still exists after recursive removal",
                str(workspace),
            )
            raise self._cleanup_error(workspace, error) from error

    def __enter__(self) -> SourceLease:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        if exc_type is None:
            self.release()
            return False
        try:
            self.release()
        except OSError:
            # The body exception owns this exit. release() left the lease
            # retryable, so orchestration may attempt cleanup again later.
            pass
        return False

    @staticmethod
    def _validate_owned_workspace(workspace: Path) -> os.stat_result:
        try:
            workspace_stat = _lstat(workspace)
        except FileNotFoundError as exc:
            raise ValueError(
                f"TEMPORARY SourceLease workspace must already exist: {workspace}"
            ) from exc
        except OSError as exc:
            raise ValueError(
                f"Unable to validate TEMPORARY SourceLease workspace {workspace}: {exc}"
            ) from exc
        if _is_link_or_reparse(workspace_stat):
            raise ValueError(
                "TEMPORARY SourceLease workspace must be a real directory, "
                f"not a symlink, junction, or reparse point: {workspace}"
            )
        if not stat.S_ISDIR(workspace_stat.st_mode):
            raise ValueError(
                f"TEMPORARY SourceLease workspace must be a directory: {workspace}"
            )
        return workspace_stat

    @staticmethod
    def _cleanup_error(workspace: Path, cause: OSError) -> OSError:
        code = cause.errno if cause.errno is not None else errno.EIO
        return OSError(
            code,
            f"Failed to release owned audio workspace {workspace}: {cause}",
            str(workspace),
        )

    @staticmethod
    def _readonly_cleanup_callback(
        workspace: Path,
    ) -> Callable[[Callable[..., Any], str, Any], None]:
        workspace_absolute = Path(os.path.abspath(str(workspace)))

        def retry_readonly(
            operation: Callable[..., Any],
            failed_path: str,
            exc_info: Any,
        ) -> None:
            cause = exc_info[1]
            candidate = Path(os.path.abspath(failed_path))
            if not (
                candidate == workspace_absolute
                or workspace_absolute in candidate.parents
            ):
                raise OSError(
                    errno.EPERM,
                    "Refusing to change permissions outside owned audio workspace",
                    failed_path,
                ) from cause
            if not isinstance(cause, OSError) or cause.errno not in (
                errno.EACCES,
                errno.EPERM,
            ):
                raise cause

            try:
                components = [workspace_absolute]
                if candidate != workspace_absolute:
                    relative_parts = candidate.parts[len(workspace_absolute.parts) :]
                    current = workspace_absolute
                    for part in relative_parts:
                        current = current / part
                        components.append(current)

                validated_stats = {}
                for component in components:
                    component_stat = _lstat(component)
                    if _is_link_or_reparse(component_stat):
                        raise OSError(
                            errno.EPERM,
                            "Refusing read-only cleanup through a link or "
                            "reparse component",
                            str(component),
                        )
                    validated_stats[component] = component_stat

                permission_paths = [candidate]
                parent = candidate.parent
                if (
                    parent == workspace_absolute
                    or workspace_absolute in parent.parents
                ):
                    permission_paths.append(parent)
                for permission_path in permission_paths:
                    permission_stat = validated_stats[permission_path]
                    additions = stat.S_IWRITE
                    if stat.S_ISDIR(permission_stat.st_mode):
                        additions |= stat.S_IEXEC
                    current_mode = stat.S_IMODE(permission_stat.st_mode)
                    chmod_kwargs = {}
                    if os.chmod in os.supports_follow_symlinks:
                        chmod_kwargs["follow_symlinks"] = False
                    os.chmod(
                        str(permission_path),
                        current_mode | additions,
                        **chmod_kwargs,
                    )
                operation(failed_path)
            except OSError as retry_error:
                raise retry_error from cause

        return retry_readonly


class AudioSourceResolver:
    """Resolve a transcription request to an exact media path under a source lease.

    Valid ``verified_media`` is reused with zero downloader calls. Otherwise the
    resolver owns a unique temporary workspace and downloads ``bestaudio`` through
    the injected ``DownloadManager``. ``CookiesRequiredError`` is preserved for
    UI-mediated cookies.txt retry after the failed attempt's workspace is gone.
    """

    def __init__(self, download_manager: Any | None = None) -> None:
        self._download_manager = download_manager

    def resolve(
        self,
        request: TranscriptionRequest,
        progress_hook: ProgressHook | None = None,
    ) -> SourceLease:
        """Return a reused lease for current verified media, else acquire audio."""
        lease = self._try_reuse(request)
        if lease is not None:
            return lease
        return self._acquire_temporary(request, progress_hook=progress_hook)

    def _try_reuse(self, request: TranscriptionRequest) -> SourceLease | None:
        media = request.verified_media
        if media is None:
            return None
        if not media.is_current():
            return None
        return SourceLease(
            path=Path(media.path),
            kind=SourceKind.REUSED,
            workspace=None,
        )

    def _acquire_temporary(
        self,
        request: TranscriptionRequest,
        progress_hook: ProgressHook | None = None,
    ) -> SourceLease:
        """Download ``bestaudio`` into an owned workspace and return a lease.

        Pre-lease failures remove only the workspace created for this attempt.
        Returned leases remain idempotently releasable and own that workspace.
        """
        manager = self._download_manager
        if manager is None:
            raise AudioAcquisitionError(_MISSING_MANAGER_MESSAGE)

        workspace = Path(tempfile.mkdtemp(prefix=_WORKSPACE_PREFIX))
        reported_paths: list[str] = []

        def post_hook(filepath: str) -> None:
            if filepath:
                reported_paths.append(str(filepath))

        outtmpl = str(workspace / _OUTTMPL_NAME)
        try:
            try:
                ok = manager.download(
                    request.source_url,
                    _BESTAUDIO_FORMAT,
                    outtmpl,
                    progress_hook=progress_hook,
                    post_hook=post_hook,
                )
            except CookiesRequiredError:
                raise
            except AudioAcquisitionError:
                raise
            except Exception as exc:
                raise AudioAcquisitionError(_GENERIC_ACQUISITION_MESSAGE) from exc

            if not ok:
                raise AudioAcquisitionError(_DOWNLOAD_FAILED_MESSAGE)

            source_path = self._select_usable_source(workspace, reported_paths)
            try:
                return SourceLease(
                    path=source_path,
                    kind=SourceKind.TEMPORARY,
                    workspace=workspace,
                )
            except Exception as exc:
                raise AudioAcquisitionError(_LEASE_FAILED_MESSAGE) from exc
        except CookiesRequiredError:
            _remove_owned_workspace(workspace)
            raise
        except AudioAcquisitionError:
            _remove_owned_workspace(workspace)
            raise
        except Exception as exc:
            _remove_owned_workspace(workspace)
            raise AudioAcquisitionError(_GENERIC_ACQUISITION_MESSAGE) from exc

    def _select_usable_source(
        self,
        workspace: Path,
        reported_paths: list[str],
    ) -> Path:
        workspace_resolved = workspace.resolve(strict=True)
        if reported_paths:
            # Post-hook reports are authoritative. Every report must normalize
            # to a safe existing file in the workspace; one bad report fails
            # the attempt rather than silently falling back to a directory scan.
            usable = self._collect_reported_paths(
                reported_paths,
                workspace_resolved,
            )
            if len(usable) > 1:
                raise AudioAcquisitionError(_AMBIGUOUS_OUTPUT_MESSAGE)
            return usable[0]

        scanned = self._scan_owned_workspace(workspace_resolved)
        if not scanned:
            raise AudioAcquisitionError(_NO_OUTPUT_MESSAGE)
        if len(scanned) > 1:
            raise AudioAcquisitionError(_AMBIGUOUS_OUTPUT_MESSAGE)
        return scanned[0]

    def _collect_reported_paths(
        self,
        candidates: list[str],
        workspace_resolved: Path,
    ) -> list[Path]:
        usable: list[Path] = []
        seen: set[str] = set()
        for raw in candidates:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = workspace_resolved / candidate
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise AudioAcquisitionError(_UNSAFE_OUTPUT_MESSAGE) from exc
            if not _is_strict_descendant(resolved, workspace_resolved):
                raise AudioAcquisitionError(_OUTSIDE_OUTPUT_MESSAGE)
            validated = self._validate_candidate(candidate, workspace_resolved)
            if validated is None:
                raise AudioAcquisitionError(_UNSAFE_OUTPUT_MESSAGE)
            # normcase supplies Windows case-insensitive semantics while
            # retaining distinct resolved pathnames (including hard links).
            path_key = os.path.normcase(os.path.normpath(str(validated)))
            if path_key in seen:
                continue
            seen.add(path_key)
            usable.append(validated)
        return usable

    def _scan_owned_workspace(self, workspace_resolved: Path) -> list[Path]:
        """Scan one owned directory while excluding known yt-dlp sidecars."""
        usable: list[Path] = []
        try:
            entries = list(workspace_resolved.iterdir())
        except OSError as exc:
            raise AudioAcquisitionError(_NO_OUTPUT_MESSAGE) from exc

        for entry in entries:
            validated = self._validate_candidate(entry, workspace_resolved)
            if validated is not None:
                usable.append(validated)
        return usable

    def _validate_candidate(
        self,
        candidate: Path,
        workspace_resolved: Path,
    ) -> Path | None:
        if (
            _is_partial_artifact_name(candidate.name)
            or _is_known_sidecar_name(candidate.name)
        ):
            return None
        try:
            candidate_stat = _lstat(candidate)
        except (FileNotFoundError, OSError):
            return None
        if _is_link_or_reparse(candidate_stat):
            return None
        if not stat.S_ISREG(candidate_stat.st_mode):
            return None
        if candidate_stat.st_size <= 0:
            return None
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None
        if not _is_strict_descendant(resolved, workspace_resolved):
            return None
        return resolved
