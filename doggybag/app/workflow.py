"""Synchronous, view-free fetch/download decisions shared by the GUI and the CLI.

Every function here runs to completion on the calling thread, reports through
injected ``log``/``log_error`` callables, and asks the user only through a
``Prompter``. The GUI controller answers with dialogs; the headless CLI answers
from command-line flags and never blocks.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ..core.dev_defaults import dev_credentials_for_url, resolve_login_plan
from ..core.error_classification import classify_error
from ..core.errors import CookiesRequiredError, NoTranscriptAvailableError
from ..core.extractors import build_media_entries, title_from_page_url
from ..core.fetcher import (
    DirectHlsFound,
    FetchError,
    FetchResults,
    NeedsCookies,
    NeedsLogin,
    NeedsSelenium,
)
from ..core.gdcvault import is_gdcvault_url
from ..core.helpers import (
    download_with_ffmpeg,
    entry_watch_url,
    is_audio_extract_format,
    sanitize_filename,
    unique_output_path,
)
from ..core.selenium_fallback import selenium_fetch_media_entries
from ..core.udio import is_udio_url
from ..core.whisper_transcription import VerifiedMedia

LogFn = Callable[[str], None]
LogErrorFn = Callable[[str, Exception], None]

DEFAULT_NAME_TEMPLATE = "%(title)s.%(ext)s"
TRANSCRIPT_NAME_TEMPLATE = "%(title)s.transcript.%(ext)s"

# Intermediate files yt-dlp leaves while merging; never reported as outputs.
_IGNORED_OUTPUT_SUFFIXES = (
    ".part",
    ".ytdl",
    ".temp",
    ".tmp",
    ".f136.mp4",
    ".f251.webm",
)


class Prompter(Protocol):
    """Questions the workflow may need answered mid-flight."""

    def cookie_retry(self) -> bool:
        """Return True to retry once after a site demanded cookies."""

    def format_fallback(self, *, extract_audio: bool) -> bool:
        """Return True to retry with ``best`` after the format was unavailable."""

    def ffmpeg_missing(self, fmt_label: str) -> str:
        """Return ``"fallback"``, ``"continue"`` or ``"cancel"``."""

    def credentials(self, url: str, *, browser_automation: bool) -> tuple[str | None, str | None]:
        """Return ``(username, password)``, or ``(None, None)`` when declined."""

    def allow_browser_automation(self) -> bool:
        """Return True to open a browser and scrape the page with Selenium."""


# ──────────────────────────────────────────────────────────
# Entries
# ──────────────────────────────────────────────────────────
def load_dev_defaults() -> dict:
    from ..core.runtime import resolve_developer_json

    path = resolve_developer_json()
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def sanitize_entries(entries, *, log: LogFn) -> list[dict]:
    raw_entries = list(entries or [])
    sanitized = [entry for entry in raw_entries if isinstance(entry, dict)]
    dropped = len(raw_entries) - len(sanitized)
    if dropped:
        suffix = "entry" if dropped == 1 else "entries"
        log(f"Dropped {dropped} unusable {suffix}.")
    return sanitized


def collection_title(entries: list[dict]) -> str | None:
    """Playlist name, or the one channel every entry shares."""
    if len(entries) < 2:
        return None
    for key in ("playlist_title", "playlist"):
        for entry in entries[:5]:
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("channel", "uploader"):
        values = {entry.get(key) for entry in entries}
        if len(values) == 1:
            value = values.pop()
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def entry_url(entry, *, fallback_url: str | None) -> str | None:
    """Per-item URL; ``fallback_url`` is only passed when there is a single entry."""
    resolved = entry_watch_url(entry)
    if resolved:
        return resolved
    return fallback_url or None


def transcript_tracks(manager, entry):
    if not isinstance(entry, dict):
        return None
    has_transcript_metadata = "subtitles" in entry or "automatic_captions" in entry
    if not has_transcript_metadata:
        return None
    return manager.transcript_tracks_from_info(entry)


def is_direct_entry(entry) -> bool:
    """Direct media (page scan, HLS, browser) downloads through ffmpeg, not yt-dlp."""
    return isinstance(entry, dict) and bool(entry.get("_ffmpeg_headers"))


# ──────────────────────────────────────────────────────────
# Fetch
# ──────────────────────────────────────────────────────────
@dataclass
class FetchResolution:
    url: str
    entries: list[dict] = field(default_factory=list)
    # "yt-dlp" | "udio" | "gdcvault" | "page-scan" | "hls-scan" | "browser" | "none"
    source: str = "none"
    error: Exception | None = None
    # "cookies" | "credentials" | "browser" when a question was declined.
    declined: str | None = None
    browser_tried: bool = False


def selenium_entries(page_url: str, username, password, *, dev_defaults: dict, log: LogFn) -> list[dict]:
    if not username or not password:
        log("Selenium fallback skipped: no credentials provided.")
        return []
    login_plan = resolve_login_plan(page_url, dev_defaults)
    entries = selenium_fetch_media_entries(page_url, username, password, login_plan=login_plan, log=log)
    if entries:
        return entries
    log("Selenium fallback error: failed to find media URLs after login")
    return []


def _results_source(url: str, outcome: FetchResults) -> str:
    if is_udio_url(url):
        return "udio"
    if is_gdcvault_url(url):
        return "gdcvault"
    if outcome.error is not None:
        return "page-scan"
    return "yt-dlp"


def resolve_fetch(
    url: str,
    *,
    fetcher,
    prompter: Prompter,
    log: LogFn,
    log_error: LogErrorFn,
    dev_defaults: dict,
    selenium_fetch: Callable[[str, str, str], list[dict]] | None = None,
) -> FetchResolution:
    """Turn a URL into entries, retrying cookies, login and browser automation as allowed."""
    resolution = FetchResolution(url=url)
    username, password = dev_credentials_for_url(url, dev_defaults)

    outcome = fetcher.fetch(url, username, password)
    if isinstance(outcome, NeedsCookies):
        error_to_log = outcome.error
        if isinstance(outcome.error, CookiesRequiredError) and outcome.error.original:
            error_to_log = outcome.error.original
        log_error("yt-dlp", error_to_log)
        resolution.error = outcome.error
        if not prompter.cookie_retry():
            resolution.declined = "cookies"
            return resolution
        outcome = fetcher.fetch(url, username, password)
        if isinstance(outcome, NeedsCookies):
            log("Browser cookie retry did not resolve the fetch error.")
            resolution.error = outcome.error
            resolution.declined = "cookies"
            return resolution

    if isinstance(outcome, NeedsLogin):
        log(str(outcome.error))
        resolution.error = outcome.error
        username, password = prompter.credentials(url, browser_automation=False)
        if not username or not password:
            log("No credentials provided.")
            resolution.declined = "credentials"
            return resolution
        outcome = fetcher.fetch(url, username, password)

    if isinstance(outcome, FetchResults):
        if outcome.error:
            log_error("yt-dlp", outcome.error)
        resolution.entries = list(outcome.entries or [])
        resolution.source = _results_source(url, outcome)
        resolution.error = outcome.error
        return resolution

    if isinstance(outcome, DirectHlsFound):
        if outcome.error:
            log("[Fallback] yt-dlp native extraction failed; using direct HLS fallback.")
        resolution.entries = build_media_entries(
            [outcome.result.playlist_url],
            page_title=title_from_page_url(url),
            headers=outcome.result.headers,
        )
        resolution.source = "hls-scan"
        resolution.error = outcome.error
        return resolution

    if isinstance(outcome, NeedsSelenium):
        if outcome.error:
            log_error("yt-dlp", outcome.error)
        resolution.error = outcome.error
        if not prompter.allow_browser_automation():
            resolution.declined = "browser"
            return resolution
        username, password = prompter.credentials(url, browser_automation=True)
        if not username or not password:
            log("No credentials provided.")
            resolution.declined = "credentials"
            return resolution
        resolution.browser_tried = True
        if selenium_fetch is None:
            entries = selenium_entries(url, username, password, dev_defaults=dev_defaults, log=log)
        else:
            entries = selenium_fetch(url, username, password)
        if entries:
            resolution.entries = list(entries)
            resolution.source = "browser"
            return resolution
        log("No media found with Selenium.")
        return resolution

    if isinstance(outcome, FetchError):
        log_error("Fetch", outcome.error)
        resolution.error = outcome.error
    elif isinstance(outcome, NeedsLogin):
        resolution.error = outcome.error
    return resolution


# ──────────────────────────────────────────────────────────
# Outputs
# ──────────────────────────────────────────────────────────
def recent_outputs(folder: str, started_at: float) -> list[str]:
    try:
        candidates = []
        for entry in Path(folder).iterdir():
            if not entry.is_file():
                continue
            name = entry.name.lower()
            if any(name.endswith(suffix) for suffix in _IGNORED_OUTPUT_SUFFIXES):
                continue
            stat = entry.stat()
            if stat.st_mtime >= started_at - 1:
                candidates.append((stat.st_mtime, str(entry)))
        return [path for _mtime, path in sorted(candidates)]
    except Exception:
        return []


def recent_transcript_outputs(
    folder: str,
    started_at: float,
    *,
    recent: Callable[[str, float], list[str]] | None = None,
) -> list[str]:
    try:
        return [
            path
            for path in (recent or recent_outputs)(folder, started_at)
            if ".transcript." in Path(path).name
        ]
    except Exception:
        return []


def verify_outputs(paths: list[str], *, log: LogFn) -> list[VerifiedMedia]:
    verified = []
    seen = set()
    for path in paths:
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            file_path = Path(path)
            exists = file_path.exists()
            is_file = file_path.is_file()
            parent = file_path.parent
            parent_exists = parent.exists()
            resolved = file_path.resolve(strict=False)
            stat = file_path.stat() if is_file else None
            size = stat.st_size if stat else None
            log(
                "Verify output: "
                f"path={file_path} resolved={resolved} exists={exists} "
                f"is_file={is_file} size={size if size is not None else 'n/a'} "
                f"parent_exists={parent_exists}"
            )
            if is_file and size and size > 0:
                verified.append(
                    VerifiedMedia(
                        path=file_path,
                        size=size,
                        modified_at=stat.st_mtime,
                    )
                )
        except Exception as exc:
            log(f"Verify output error for {path}: {type(exc).__name__}: {exc}")
            continue
    return verified


def folder_snapshot(folder: str, limit: int = 10) -> str:
    try:
        entries = []
        for entry in Path(folder).iterdir():
            if not entry.is_file():
                continue
            stat = entry.stat()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
            entries.append((stat.st_mtime, f"{entry.name} ({stat.st_size:,} bytes, {timestamp})"))
        if not entries:
            return "no files"
        newest = [description for _mtime, description in sorted(entries, reverse=True)[:limit]]
        return "; ".join(newest)
    except Exception as exc:
        return f"unavailable ({type(exc).__name__}: {exc})"


# ──────────────────────────────────────────────────────────
# Media downloads
# ──────────────────────────────────────────────────────────
def resolve_merge_format(
    format_id: str,
    fmt_label: str | None,
    *,
    ffmpeg_available: bool,
    prompter: Prompter,
    log: LogFn,
    labels: dict | None = None,
) -> tuple[str, str | None] | None:
    """Apply the missing-ffmpeg policy to a merge format; None means cancelled."""
    if ffmpeg_available or "+" not in format_id:
        return format_id, fmt_label
    choice = prompter.ffmpeg_missing(fmt_label or format_id)
    if choice == "cancel":
        return None
    if choice == "fallback":
        log("ffmpeg missing: switching to Best (single file) for compatibility.")
        return "best", (labels or {}).get("best", "Best (single file)")
    log("ffmpeg missing: proceeding without merge support (may fail or be video-only).")
    return format_id, fmt_label


@dataclass
class MediaDownloadResult:
    ok: bool
    requested_format: str
    format_id: str  # last selector passed to yt-dlp ("best" after a fallback)
    fmt_label: str | None
    extract_audio: bool
    outputs: list[VerifiedMedia] = field(default_factory=list)
    # Outputs came from yt-dlp's own report, not a folder scan (Whisper provenance).
    exact: bool = False
    already_existed: bool = False
    format_fallback: bool = False
    error: Exception | None = None
    unverified: list[str] = field(default_factory=list)


def _error_reason(err: Exception | None) -> str | None:
    if err is None:
        return None
    original = getattr(err, "original", None)
    reason, _hint = classify_error(str(original or err) or "Unknown error")
    return reason


def download_media(
    manager,
    url: str,
    format_id: str,
    folder: str,
    *,
    prompter: Prompter,
    log: LogFn,
    log_error: LogErrorFn,
    fmt_label: str | None = None,
    ffmpeg_available: bool = True,
    labels: dict | None = None,
    name_template: str = DEFAULT_NAME_TEMPLATE,
    progress_hook: Callable[[dict], None] | None = None,
    on_format_fallback: Callable[[str, str], None] | None = None,
    fallback_format: str = "best",
    recent: Callable[[str, float], list[str]] | None = None,
    verify: Callable[[list[str]], list[VerifiedMedia]] | None = None,
    snapshot: Callable[[str], str] | None = None,
) -> MediaDownloadResult:
    """Download one yt-dlp item with the cookie retry and format fallback the GUI uses."""
    labels = labels or {}
    recent = recent or recent_outputs
    verify = verify or (lambda paths: verify_outputs(paths, log=log))
    snapshot = snapshot or folder_snapshot
    saved_files: list[str] = []

    def post_hook(filepath):
        if filepath:
            final_path = os.path.abspath(filepath)
            saved_files.append(final_path)
            log(f"yt-dlp reported final path: {final_path}")

    outtmpl = os.path.join(folder, name_template)
    started_at = time.time()
    wants_audio = is_audio_extract_format(format_id)
    extract_audio = wants_audio and ffmpeg_available
    if wants_audio and not ffmpeg_available:
        log("ffmpeg missing: saving audio in its original format instead of MP3.")
    result = MediaDownloadResult(
        ok=False,
        requested_format=format_id,
        format_id=format_id,
        fmt_label=fmt_label,
        extract_audio=extract_audio,
    )
    log(
        "Download runtime: "
        f"pid={os.getpid()} exe={sys.executable} cwd={os.getcwd()} "
        f"out_path={folder} outtmpl={outtmpl}"
    )
    log(f"Output folder before download: {snapshot(folder)}")

    def attempt(selector: str) -> tuple[bool, Exception | None]:
        try:
            ok = manager.download(
                url,
                selector,
                outtmpl,
                progress_hook=progress_hook,
                post_hook=post_hook,
                extract_audio=extract_audio,
                raise_errors=True,
            )
            return bool(ok), None
        except CookiesRequiredError:
            raise
        except Exception as exc:
            log_error("Download", exc)
            return False, exc

    def attempt_with_cookie_report(selector: str) -> tuple[bool, Exception | None]:
        try:
            return attempt(selector)
        except CookiesRequiredError as exc:
            log_error("Download", exc.original or exc)
            return False, exc

    try:
        ok, failure = attempt(format_id)
    except CookiesRequiredError as exc:
        log_error("Download", exc.original or exc)
        ok, failure = False, exc
        if prompter.cookie_retry():
            ok, failure = attempt_with_cookie_report(format_id)

    if (
        not ok
        and format_id != fallback_format
        and _error_reason(failure) == "format unavailable"
        and prompter.format_fallback(extract_audio=extract_audio)
    ):
        fallback_id = fallback_format
        if extract_audio:
            fallback_label = labels.get(format_id, "Audio (MP3)")
            log("Retrying by extracting MP3 from the best available file.")
        else:
            fallback_label = labels.get(fallback_id, "Best (single file)" if fallback_id == "best" else fallback_id)
            log(f"Retrying download with {fallback_label} due to unavailable format.")
        if on_format_fallback is not None:
            on_format_fallback(format_id if extract_audio else fallback_id, fallback_label)
        result.format_fallback = True
        result.format_id = fallback_id
        result.fmt_label = fallback_label
        ok, failure = attempt_with_cookie_report(fallback_id)

    if ok:
        has_reported_outputs = bool(saved_files)
        output_files = saved_files or recent(folder, started_at)
        verified_files = verify(output_files)
        log(f"Output folder after download: {snapshot(folder)}")
        if verified_files:
            all_preexisting = True
            for output in verified_files:
                was_preexisting = output.modified_at < started_at - 1
                all_preexisting = all_preexisting and was_preexisting
                status = "already existed" if was_preexisting else "saved"
                log(f"Download {status}: {output.path} ({output.size:,} bytes)")
            result.outputs = list(verified_files)
            result.exact = has_reported_outputs
            result.already_existed = all_preexisting
        else:
            ok = False
            log(
                "Download verification failed: yt-dlp returned success, "
                "but Doggybag could not confirm the final file exists."
            )
            if output_files:
                for saved_file in output_files:
                    log(f"Unverified output path: {saved_file}")
            else:
                log("yt-dlp did not report a final output path.")
            log(f"Output folder snapshot: {snapshot(folder)}")
            result.unverified = list(output_files)
    result.ok = ok
    result.error = None if ok else failure
    return result


@dataclass
class DirectMediaResult:
    ok: bool
    output_file: str
    output: VerifiedMedia | None = None
    error: Exception | None = None


def direct_output_path(folder: str, title: str, *, audio_only: bool, name: str | None = None) -> str:
    safe_title = sanitize_filename(name or title)
    return unique_output_path(folder, safe_title, "mp3" if audio_only else "mp4")


def download_direct(
    media_url: str,
    headers: dict | None,
    output_file: str,
    *,
    audio_only: bool = False,
    log: LogFn,
    log_error: LogErrorFn,
    progress_hook: Callable[[float, float | None, float | None], None] | None = None,
    verify: Callable[[list[str]], list[VerifiedMedia]] | None = None,
) -> DirectMediaResult:
    """Download direct media (HLS, page scan, browser) through ffmpeg."""
    verify = verify or (lambda paths: verify_outputs(paths, log=log))
    try:
        download_with_ffmpeg(
            media_url,
            output_file,
            headers or {},
            progress_hook=progress_hook,
            audio_only=audio_only,
        )
        verified_files = verify([output_file])
        if len(verified_files) == 1:
            verified = verified_files[0]
            log(f"ffmpeg download complete: {verified.path} ({verified.size:,} bytes)")
            return DirectMediaResult(ok=True, output_file=output_file, output=verified)
        log(
            "Download verification failed: ffmpeg reported success, "
            "but Doggybag could not confirm the final file exists."
        )
        log(f"Unverified output path: {output_file}")
        return DirectMediaResult(ok=False, output_file=output_file)
    except Exception as exc:
        log_error("ffmpeg", exc)
        return DirectMediaResult(ok=False, output_file=output_file, error=exc)


# ──────────────────────────────────────────────────────────
# Provider transcripts
# ──────────────────────────────────────────────────────────
@dataclass
class TranscriptOutcome:
    ok: bool
    status: str  # "saved" | "unavailable" | "failed"
    source: str | None = None
    languages: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    error: Exception | None = None


def download_provider_transcript(
    manager,
    url: str,
    folder: str,
    *,
    prompter: Prompter,
    log: LogFn,
    log_error: LogErrorFn,
    track=None,
    recent_transcripts: Callable[[str, float], list[str]] | None = None,
) -> TranscriptOutcome:
    """Download the site's own subtitles/captions with one cookie retry."""
    outtmpl = os.path.join(folder, TRANSCRIPT_NAME_TEMPLATE)
    started_at = time.time()
    scan = recent_transcripts or recent_transcript_outputs

    def saved(result) -> TranscriptOutcome:
        outcome = TranscriptOutcome(
            ok=True,
            status="saved",
            source=result.source,
            languages=list(result.languages),
            paths=scan(folder, started_at),
        )
        languages = ", ".join(outcome.languages[:5])
        if len(outcome.languages) > 5:
            languages += ", ..."
        detail = f" [{languages}]" if languages else ""
        log(f"Transcript download complete ({result.source}){detail}")
        return outcome

    def fetch():
        return manager.download_transcript(url, outtmpl, selected_track=track)

    try:
        return saved(fetch())
    except CookiesRequiredError as exc:
        log_error("Transcript", exc.original or exc)
        if not prompter.cookie_retry():
            return TranscriptOutcome(ok=False, status="failed", error=exc)
        try:
            return saved(fetch())
        except CookiesRequiredError as retry_exc:
            log_error("Transcript", retry_exc.original or retry_exc)
            return TranscriptOutcome(ok=False, status="failed", error=retry_exc)
        except NoTranscriptAvailableError as retry_exc:
            log_error("Transcript", retry_exc)
            return TranscriptOutcome(ok=False, status="unavailable", error=retry_exc)
        except Exception as retry_exc:
            log_error("Transcript", retry_exc)
            return TranscriptOutcome(ok=False, status="failed", error=retry_exc)
    except NoTranscriptAvailableError as exc:
        log_error("Transcript", exc)
        return TranscriptOutcome(ok=False, status="unavailable", error=exc)
    except Exception as exc:
        log_error("Transcript", exc)
        return TranscriptOutcome(ok=False, status="failed", error=exc)


__all__ = [
    "DEFAULT_NAME_TEMPLATE",
    "DirectMediaResult",
    "FetchResolution",
    "MediaDownloadResult",
    "Prompter",
    "TRANSCRIPT_NAME_TEMPLATE",
    "TranscriptOutcome",
    "collection_title",
    "direct_output_path",
    "download_direct",
    "download_media",
    "download_provider_transcript",
    "entry_url",
    "folder_snapshot",
    "is_direct_entry",
    "load_dev_defaults",
    "recent_outputs",
    "recent_transcript_outputs",
    "resolve_fetch",
    "resolve_merge_format",
    "sanitize_entries",
    "selenium_entries",
    "transcript_tracks",
    "verify_outputs",
]
