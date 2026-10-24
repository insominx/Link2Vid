"""CLI commands. Each appends results to a ``Run``; run-level failures raise ``CliError``."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..app.workflow import (
    DEFAULT_NAME_TEMPLATE,
    collection_title,
    direct_output_path,
    download_direct,
    download_media,
    download_provider_transcript,
    entry_url,
    is_direct_entry,
    resolve_merge_format,
    sanitize_entries,
    transcript_tracks,
)
from ..core.downloader import TranscriptTrack
from ..core.helpers import (
    AUDIO_EXTRACT_FORMAT,
    get_format_options,
    get_yt_dlp_version,
    is_audio_extract_format,
    normalize_url,
    sanitize_filename,
    unique_output_path,
)
from ..core.media_tools import (
    cut_section,
    parse_time_range,
    probe_media_file,
    section_extension,
    section_stem,
)
from .output import CliError, entry_summary, error_info, make_error
from .session import Session, warning

FORMAT_PRESETS = {
    "best": "bestvideo+bestaudio/best",
    "video-only": "bestvideo",
    "audio": AUDIO_EXTRACT_FORMAT,
}
TRANSCRIPT_SOURCES = {"subtitles": "subtitles", "auto": "automatic captions"}


def format_labels() -> dict[str, str]:
    labels = {opt["format"]: opt["label"] for opt in get_format_options()}
    labels.setdefault("best", "Best (single file)")
    return labels


@dataclass
class Run:
    command: str
    out_dir: str | None = None
    inputs: list[str] = field(default_factory=list)
    fetches: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    report: dict | None = None
    error: dict | None = None

    def warn_once(self, code: str, message: str) -> None:
        if not any(existing.get("code") == code for existing in self.warnings):
            self.warnings.append(warning(code, message))


# ──────────────────────────────────────────────────────────
# Shared steps
# ──────────────────────────────────────────────────────────
def parse_items(spec: str, count: int) -> list[int]:
    """``all``, ``3`` or ``1,4,6-9`` (1-based) -> sorted 0-based indexes."""
    text = (spec or "").strip().lower()
    if text == "all":
        return list(range(count))
    chosen: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        try:
            if "-" in part:
                low_text, high_text = part.split("-", 1)
                low, high = int(low_text), int(high_text)
            else:
                low = high = int(part)
        except ValueError:
            raise CliError("invalid_input", f"--items {spec!r}: use all, 3 or 1,4,6-9") from None
        if low < 1 or high < low:
            raise CliError("invalid_input", f"--items {spec!r}: indexes start at 1 and ranges go low-high")
        if high > count:
            raise CliError("invalid_input", f"--items {spec!r}: only {count} entries were found")
        chosen.update(range(low - 1, high))
    return sorted(chosen)


def _new_item(*, source_url: str, action: str, index: int | None = None, title: str | None = None,
              url: str | None = None, route: str | None = None) -> dict:
    return {
        "index": index,
        "source_url": source_url,
        "url": url,
        "title": title,
        "action": action,
        "route": route,
        "status": "running",
        "files": [],
        "warnings": [],
        "error": None,
    }


def _fetch_failure(resolution, declined: list[str], url: str) -> dict:
    if resolution.browser_tried:
        return make_error("no_media_found", "Browser automation found no media on the page.", url=url)
    if "cookies" in declined:
        message = str(getattr(resolution.error, "original", None) or resolution.error or "")
        return make_error("cookies_required", message or "The site wants a signed-in session.", url=url)
    if "credentials" in declined:
        return make_error("credentials_required", "The site needs a username and password.", url=url)
    if "browser" in declined:
        return make_error(
            "browser_automation_required",
            "Automatic extraction failed; only a browser scrape (visible Chrome) is left.",
            url=url,
        )
    if resolution.error is not None:
        info = error_info(resolution.error, url=url, stage="fetch")
        if info["code"] == "download_failed":
            info["code"] = "no_media_found"
        return info
    return make_error("no_media_found", "No videos found or unsupported site.", url=url)


def fetch_entries(session: Session, run: Run, url: str) -> list[dict] | None:
    session.log(f"Fetching {url}")
    session.reporter.event("fetch", url=url)
    resolution = session.fetch(url)
    warnings, declined = session.prompter.drain()
    entries = sanitize_entries(resolution.entries, log=session.log)
    record = {
        "url": url,
        "source": resolution.source,
        "entries": len(entries),
        "collection": collection_title(entries),
        "error": None,
    }
    run.fetches.append(record)
    run.warnings.extend(warnings)
    if entries:
        session.log(f"Found {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}.")
        return entries
    error = _fetch_failure(resolution, declined, url)
    record["error"] = error
    item = _new_item(source_url=url, action="fetch")
    item.update(status="failed", error=error)
    run.items.append(item)
    return None


def select_indexes(run: Run, url: str, entries: list[dict], spec: str | None, *, require_choice: bool) -> list[int]:
    if spec is None:
        if len(entries) > 1 and require_choice:
            listing = [
                {"index": position + 1, "title": str(entry.get("title") or "Untitled")}
                for position, entry in enumerate(entries)
            ]
            run.fetches[-1]["listing"] = listing
            item = _new_item(source_url=url, action="select")
            item.update(
                status="failed",
                error=make_error(
                    "items_required",
                    f"{len(entries)} entries found; choose which to process with --items.",
                    url=url,
                    count=len(entries),
                ),
            )
            run.items.append(item)
            return []
        return list(range(len(entries)))
    return parse_items(spec, len(entries))


def _file_record(session: Session, run: Run, path, kind: str, *, probe: bool = True, **extra) -> dict:
    file_path = Path(path)
    record = {"kind": kind, "path": str(file_path.resolve()), "size": file_path.stat().st_size}
    record.update(extra)
    if probe:
        if session.ffprobe_path:
            facts = probe_media_file(file_path)
            if facts is not None:
                record.update(facts.as_dict())
        else:
            run.warn_once("ffprobe_missing", "ffprobe is not on PATH; media facts were omitted.")
    return record


def _ytdlp_progress(session: Session, index, phase: str = "download"):
    def hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            fraction = min(done / total, 1.0) if total else None
            session.reporter.progress(index, phase, fraction, speed=d.get("speed"), eta=d.get("eta"))
        elif status == "finished":
            session.reporter.progress(index, phase, 1.0)

    return hook


def _finish_item(session: Session, item: dict, *, ok: bool) -> None:
    warnings, declined = session.prompter.drain()
    item["warnings"].extend(warnings)
    if ok:
        if item["status"] == "running":
            item["status"] = "ok"
    else:
        item["status"] = "failed"
        if "ffmpeg" in declined:
            item["error"] = make_error(
                "ffmpeg_missing",
                "ffmpeg is missing and --if-no-ffmpeg is fail.",
                url=item.get("url"),
            )
        elif item["error"] is None:
            item["error"] = make_error("download_failed", "The download failed.", url=item.get("url"))
    session.reporter.event("item_done", index=item["index"], status=item["status"])


def _entry_title(entry: dict) -> str:
    return str(entry.get("title") or "Untitled")


# ──────────────────────────────────────────────────────────
# info
# ──────────────────────────────────────────────────────────
def cmd_info(session: Session, run: Run, args) -> None:
    run.report = {
        "presets": [{"name": name, "format": fmt} for name, fmt in FORMAT_PRESETS.items()],
    }
    for raw in args.urls:
        url = normalize_url(raw)
        entries = fetch_entries(session, run, url)
        if not entries:
            continue
        indexes = select_indexes(run, url, entries, args.items, require_choice=False)
        include_formats = args.formats or len(entries) == 1
        fallback = url if len(entries) <= 1 else None
        for position in indexes:
            entry = entries[position]
            summary = entry_summary(
                entry,
                index=position + 1,
                fallback_url=fallback,
                tracks=transcript_tracks(session.manager, entry),
                include_formats=include_formats,
            )
            run.items.append(
                {
                    **summary,
                    "source_url": url,
                    "action": "info",
                    "status": "ok",
                    "files": [],
                    "warnings": [],
                    "error": None,
                }
            )


# ──────────────────────────────────────────────────────────
# download
# ──────────────────────────────────────────────────────────
def resolve_format(args) -> str:
    if args.audio:
        return AUDIO_EXTRACT_FORMAT
    if args.video_only:
        return FORMAT_PRESETS["video-only"]
    if args.format:
        return FORMAT_PRESETS.get(args.format, args.format)
    return FORMAT_PRESETS["best"]


def _name_template(name: str | None) -> str:
    if not name:
        return DEFAULT_NAME_TEMPLATE
    return sanitize_filename(name).replace("%", "%%") + ".%(ext)s"


def cmd_download(session: Session, run: Run, args) -> None:
    fmt = resolve_format(args)
    try:
        sections = [parse_time_range(spec) for spec in (args.section or [])]
    except ValueError as exc:
        raise CliError("invalid_input", str(exc)) from None
    if sections and not session.ffmpeg_available:
        raise CliError("ffmpeg_missing", "Cutting --section clips needs ffmpeg on PATH.")
    if args.name and len(args.urls) > 1:
        raise CliError("invalid_input", "--name works with exactly one URL and one item.")
    for raw in args.urls:
        url = normalize_url(raw)
        entries = fetch_entries(session, run, url)
        if not entries:
            continue
        indexes = select_indexes(run, url, entries, args.items, require_choice=True)
        if args.name and len(indexes) > 1:
            raise CliError("invalid_input", "--name works with exactly one URL and one item.")
        for position in indexes:
            _download_one(session, run, args, url, entries, position, fmt, sections)


def _download_one(session: Session, run: Run, args, url: str, entries: list[dict], position: int,
                  fmt: str, sections: list[tuple[float, float | None]]) -> None:
    entry = entries[position]
    index = position + 1
    direct = is_direct_entry(entry)
    title = _entry_title(entry)
    item_url = entry_url(entry, fallback_url=url if len(entries) <= 1 else None)
    item = _new_item(
        source_url=url,
        action="download",
        index=index,
        title=title,
        url=None if direct else item_url,
        route="direct" if direct else "yt-dlp",
    )
    run.items.append(item)
    session.reporter.event("item_start", index=index, title=title)
    session.log(f"[{index}] Downloading: {title}")
    if item_url is None:
        item["error"] = make_error("no_media_found", "Entry has no resolvable per-item URL.", url=url)
        _finish_item(session, item, ok=False)
        return

    audio = is_audio_extract_format(fmt)
    out_dir = run.out_dir
    workspace = tempfile.mkdtemp(prefix=".doggybag-section-", dir=out_dir) if sections else None
    target = workspace or out_dir
    ok = False
    try:
        if direct:
            path = direct_output_path(target, title, audio_only=audio, name=args.name)
            result = download_direct(
                item_url,
                entry.get("_ffmpeg_headers"),
                path,
                audio_only=audio,
                log=session.log,
                log_error=session.log_error,
                progress_hook=lambda fraction, *_: session.reporter.progress(index, "ffmpeg", fraction),
            )
            effective = "ffmpeg-audio" if audio else "ffmpeg"
            item["format"] = {
                "requested": fmt,
                "effective": effective,
                "label": "Direct audio (MP3)" if audio else "Direct media",
                "extract_audio": audio,
                "substituted": False,
            }
            ok = result.ok
            outputs = [result.output.path] if ok else []
            already = False
            if not ok:
                item["error"] = (
                    error_info(result.error, url=url, stage="ffmpeg")
                    if result.error
                    else make_error("verification_failed", "ffmpeg finished but no file was found.", url=url)
                )
        else:
            labels = format_labels()
            merge = resolve_merge_format(
                fmt,
                labels.get(fmt, fmt),
                ffmpeg_available=session.ffmpeg_available,
                prompter=session.prompter,
                log=session.log,
                labels=labels,
            )
            if merge is None:
                _finish_item(session, item, ok=False)
                return
            effective, label = merge
            result = download_media(
                session.manager,
                item_url,
                effective,
                target,
                prompter=session.prompter,
                log=session.log,
                log_error=session.log_error,
                fmt_label=label,
                ffmpeg_available=session.ffmpeg_available,
                labels=labels,
                name_template=_name_template(args.name),
                progress_hook=_ytdlp_progress(session, index),
                # Many YouTube videos no longer offer a single muxed file, so a
                # bare "best" fallback fails there; merge when ffmpeg allows it.
                fallback_format=FORMAT_PRESETS["best"]
                if session.ffmpeg_available and not is_audio_extract_format(effective)
                else "best",
            )
            item["player_client"] = session.manager.last_player_client
            item["cookies"] = session.cookies_report()
            item["format"] = {
                "requested": fmt,
                "effective": result.format_id,
                "label": result.fmt_label,
                "extract_audio": result.extract_audio,
                "substituted": result.format_id != fmt,
            }
            if session.manager.last_player_client:
                item["warnings"].append(
                    warning(
                        "player_client_fallback",
                        f"Succeeded with YouTube player client '{session.manager.last_player_client}'.",
                    )
                )
            ok = result.ok
            outputs = [output.path for output in result.outputs]
            already = result.already_existed
            if not ok:
                if result.error is not None:
                    item["error"] = error_info(result.error, url=item_url, stage="download")
                elif result.unverified or not result.outputs:
                    item["error"] = make_error(
                        "verification_failed",
                        "yt-dlp reported success but Doggybag could not find the file.",
                        url=item_url,
                    )
        if ok:
            if sections:
                _cut_sections(session, run, args, item, outputs[0], sections, audio=audio)
            else:
                item["files"] = [_file_record(session, run, path, "media") for path in outputs]
                if already:
                    item["status"] = "already_exists"
    except (CliError, OSError, RuntimeError) as exc:
        ok = False
        item["error"] = error_info(exc, url=url, stage="section" if sections else "download")
    finally:
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)
    _finish_item(session, item, ok=ok and item["error"] is None)


def _cut_sections(session: Session, run: Run, args, item: dict, source, sections, *, audio: bool) -> None:
    source = Path(source)
    stem = sanitize_filename(args.name) if args.name else source.stem
    facts = probe_media_file(source) if session.ffprobe_path else None
    duration = facts.duration if facts else None
    for start, end in sections:
        if duration is not None and start >= duration:
            raise CliError(
                "invalid_input",
                f"Section starts at {start:g}s but the media is only {duration:g}s long.",
            )
        if duration is not None and end is not None and end > duration:
            item["warnings"].append(
                warning("section_clamped", f"Section end {end:g}s is past the media end; cut to {duration:g}s.")
            )
            end = duration
        ext = section_extension(source, mode=args.section_mode, audio_only=audio)
        clip = unique_output_path(run.out_dir, section_stem(stem, start, end), ext)
        session.log(f"[{item['index']}] Cutting {start:g}-{'end' if end is None else f'{end:g}'} -> {clip}")
        session.reporter.progress(item["index"], "cut", None)
        cut_section(source, clip, start, end, mode=args.section_mode, audio_only=audio)
        item["files"].append(
            _file_record(
                session,
                run,
                clip,
                "clip",
                section={"start": start, "end": end, "mode": args.section_mode},
            )
        )
    if args.section_mode == "copy":
        item["warnings"].append(
            warning("section_keyframes", "Copy mode starts each clip on the previous keyframe.")
        )
    if args.keep_full:
        destination = unique_output_path(run.out_dir, source.stem, source.suffix.lstrip(".") or "mp4")
        shutil.move(str(source), destination)
        item["files"].append(_file_record(session, run, destination, "media"))


# ──────────────────────────────────────────────────────────
# transcript (site captions)
# ──────────────────────────────────────────────────────────
def _preferred(tracks: list[TranscriptTrack]) -> TranscriptTrack | None:
    for test in (
        lambda t: t.language.lower() == "en",
        lambda t: t.language.lower().startswith("en-"),
        lambda t: True,
    ):
        for track in tracks:
            if test(track):
                return track
    return None


def choose_track(tracks, lang: str | None, source: str | None) -> TranscriptTrack | None:
    source_name = TRANSCRIPT_SOURCES.get(source) if source else None
    if not lang and not source_name:
        return None
    if tracks is None:
        if lang:
            return TranscriptTrack(source=source_name or "subtitles", language=lang, label=lang)
        return None
    candidates = [track for track in tracks if source_name is None or track.source == source_name]
    chosen = None
    # Real subtitles beat automatic captions; within a source, the exact code beats a
    # regional variant (en, then en-US).
    for source_tracks in (
        [track for track in candidates if track.source == "subtitles"],
        [track for track in candidates if track.source != "subtitles"],
    ):
        if lang:
            wanted = lang.lower()
            exact = [track for track in source_tracks if track.language.lower() == wanted]
            variants = [track for track in source_tracks if track.language.lower().startswith(f"{wanted}-")]
            chosen = (exact or variants or [None])[0]
        else:
            chosen = _preferred(source_tracks)
        if chosen is not None:
            break
    if chosen is None:
        available = _available_summary(tracks)
        what = f"'{lang}'" if lang else "any language"
        where = f" in {source_name}" if source_name else ""
        raise CliError(
            "no_transcript",
            f"No transcript track for {what}{where}.",
            next_steps=[{"command": "transcript --list", "note": "Available: " + (", ".join(available) or "none")}],
            available=available,
        )
    return chosen


def _available_summary(tracks, limit: int = 25) -> list[str]:
    """Subtitles first, then automatic captions; auto-translations make this list long."""
    ordered = sorted(tracks, key=lambda track: (track.source != "subtitles", track.language.lower()))
    labels = list(dict.fromkeys(f"{track.language} ({track.source})" for track in ordered))
    if len(labels) > limit:
        labels = labels[:limit] + [f"... {len(labels) - limit} more (run transcript --list)"]
    return labels


def cmd_transcript(session: Session, run: Run, args) -> None:
    for raw in args.urls:
        url = normalize_url(raw)
        entries = fetch_entries(session, run, url)
        if not entries:
            continue
        indexes = select_indexes(run, url, entries, args.items, require_choice=not args.list)
        for position in indexes:
            entry = entries[position]
            index = position + 1
            item_url = entry_url(entry, fallback_url=url if len(entries) <= 1 else None)
            item = _new_item(
                source_url=url,
                action="list" if args.list else "transcript",
                index=index,
                title=_entry_title(entry),
                url=item_url,
                route="yt-dlp",
            )
            run.items.append(item)
            session.reporter.event("item_start", index=index, title=item["title"])
            tracks = transcript_tracks(session.manager, entry)
            if args.list:
                item["transcript_tracks"] = (
                    None
                    if tracks is None
                    else [{"source": t.source, "language": t.language, "label": t.label} for t in tracks]
                )
                if tracks is None:
                    item["warnings"].append(
                        warning("tracks_unknown", "Track list is unavailable for flat entries; rerun without --flat.")
                    )
                _finish_item(session, item, ok=True)
                continue
            try:
                track = choose_track(tracks, args.lang, args.source)
            except CliError as exc:
                item["error"] = error_info(exc, url=item_url)
                _finish_item(session, item, ok=False)
                continue
            outcome = download_provider_transcript(
                session.manager,
                item_url,
                run.out_dir,
                prompter=session.prompter,
                log=session.log,
                log_error=session.log_error,
                track=track,
            )
            item["cookies"] = session.cookies_report()
            if outcome.ok:
                item["transcript"] = {"source": outcome.source, "languages": outcome.languages}
                item["files"] = [
                    _file_record(session, run, path, "transcript", probe=False) for path in outcome.paths
                ]
                if not outcome.paths:
                    item["warnings"].append(
                        warning(
                            "transcript_paths_unknown",
                            "yt-dlp saved the transcript but its file was not found (it may already exist).",
                        )
                    )
            else:
                item["error"] = error_info(outcome.error, url=item_url, stage="transcript") if outcome.error else (
                    make_error("no_transcript", "No transcript was saved.", url=item_url)
                )
            _finish_item(session, item, ok=outcome.ok)


# ──────────────────────────────────────────────────────────
# transcribe (local Whisper)
# ──────────────────────────────────────────────────────────
def _whisper_model(session: Session, requested: str | None) -> str:
    from ..core.whisper_transcription import default_model_name

    try:
        models = session.whisper_service().list_available_models()
    except Exception as exc:
        raise CliError("whisper_unavailable", f"Faster Whisper is unavailable: {exc}") from None
    if not models:
        raise CliError("whisper_unavailable", "Faster Whisper reported no models.")
    model = requested or default_model_name(models)
    if model not in models:
        raise CliError(
            "whisper_model_invalid",
            f"Unknown Whisper model {model!r}.",
            next_steps=[{"add_flags": ["--model", "tiny"], "note": "Available: " + ", ".join(models)}],
            available=list(models),
        )
    return model


def cmd_transcribe(session: Session, run: Run, args) -> None:
    model = _whisper_model(session, args.model)
    for target in args.inputs:
        if os.path.isfile(target):
            _transcribe_file(session, run, target, model)
            continue
        url = normalize_url(target)
        entries = fetch_entries(session, run, url)
        if not entries:
            continue
        for position in select_indexes(run, url, entries, args.items, require_choice=True):
            _transcribe_entry(session, run, url, entries, position, model)


def _verified(path: Path):
    from ..core.whisper_transcription import VerifiedMedia

    stat = path.stat()
    return VerifiedMedia(path=path, size=stat.st_size, modified_at=stat.st_mtime)


def _request(source_url: str, title: str, out_dir: str, model: str, verified):
    from ..core.whisper_transcription import TranscriptionRequest

    return TranscriptionRequest(
        request_id=uuid.uuid4().hex,
        source_url=source_url,
        title=title,
        output_dir=Path(out_dir),
        model_name=model,
        verified_media=verified,
    )


def _transcribe_file(session: Session, run: Run, target: str, model: str) -> None:
    path = Path(target).resolve()
    item = _new_item(source_url=str(path), action="transcribe", title=path.stem, route="local")
    run.items.append(item)
    session.reporter.event("item_start", index=None, title=path.stem)
    request = _request(path.as_uri(), path.stem, run.out_dir, model, _verified(path))
    _run_whisper(session, item, request)


def _transcribe_entry(session: Session, run: Run, url: str, entries: list[dict], position: int, model: str) -> None:
    entry = entries[position]
    index = position + 1
    direct = is_direct_entry(entry)
    item_url = entry_url(entry, fallback_url=url if len(entries) <= 1 else None)
    title = _entry_title(entry)
    item = _new_item(
        source_url=url,
        action="transcribe",
        index=index,
        title=title,
        url=None if direct else item_url,
        route="direct" if direct else "yt-dlp",
    )
    run.items.append(item)
    session.reporter.event("item_start", index=index, title=title)
    if item_url is None:
        item["error"] = make_error("no_media_found", "Entry has no resolvable per-item URL.", url=url)
        _finish_item(session, item, ok=False)
        return
    if not direct:
        _run_whisper(session, item, _request(item_url, title, run.out_dir, model, None))
        return
    # Direct media needs its ffmpeg headers, so fetch the audio here, not in the resolver.
    workspace = tempfile.mkdtemp(prefix="doggybag-cli-audio-")
    try:
        audio_path = direct_output_path(workspace, title, audio_only=True)
        result = download_direct(
            item_url,
            entry.get("_ffmpeg_headers"),
            audio_path,
            audio_only=True,
            log=session.log,
            log_error=session.log_error,
            progress_hook=lambda fraction, *_: session.reporter.progress(index, "audio", fraction),
        )
        if not result.ok:
            item["error"] = (
                error_info(result.error, url=url, stage="audio")
                if result.error
                else make_error("audio_acquisition_failed", "ffmpeg finished but no audio file was found.", url=url)
            )
            _finish_item(session, item, ok=False)
            return
        _run_whisper(session, item, _request(url, title, run.out_dir, model, _verified(Path(audio_path))))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _run_whisper(session: Session, item: dict, request) -> None:
    index = item["index"]

    def on_phase(phase):
        name = getattr(phase, "value", str(phase))
        session.reporter.event("phase", index=index, phase=name)
        session.log(f"[{index if index is not None else '-'}] whisper: {name}")

    service = session.whisper_service()
    try:
        lease = session.audio_resolver().resolve(request, progress_hook=_ytdlp_progress(session, index, "audio"))
        with lease:
            result = service.transcribe(request, lease.path, on_phase=on_phase)
    except Exception as exc:
        info = error_info(exc, url=item.get("url"), stage="transcribe")
        if info["code"] == "download_failed":
            info["code"] = "transcription_failed"
        item["error"] = info
        _finish_item(session, item, ok=False)
        return
    item["whisper"] = {
        "model": result.model_name,
        "language": result.language,
        "segments": result.segment_count,
        "device": result.effective_device,
        "cpu_fallback": result.fallback_used,
    }
    item["files"] = [
        _file_record(session, None, result.text_path, "whisper_text", probe=False),
        _file_record(session, None, result.srt_path, "whisper_srt", probe=False),
    ]
    _finish_item(session, item, ok=True)


# ──────────────────────────────────────────────────────────
# doctor
# ──────────────────────────────────────────────────────────
def _version_of(distribution: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(distribution)
    except Exception:
        return None


def _ffmpeg_version(path: str | None) -> str | None:
    if not path:
        return None
    try:
        proc = subprocess.run([path, "-version"], capture_output=True, text=True, check=False, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    first = (proc.stdout or "").splitlines()
    return first[0].strip() if first else None


def cmd_doctor(session: Session, run: Run, args) -> None:
    from ..core.cookie_prefs import user_settings_path
    from ..core.runtime import app_dir, is_frozen, model_cache_dir, resolve_developer_json

    js_runtime, js_path = session.manager._select_js_runtime()
    whisper: dict = {"available": False}
    try:
        from ..core.whisper_transcription import default_model_name

        models = session.whisper_service().list_available_models()
        cache = model_cache_dir()
        cached = sorted(
            entry.name.removeprefix("models--")
            for entry in (cache.iterdir() if cache.is_dir() else [])
            if entry.is_dir() and entry.name.startswith("models--")
        )
        whisper = {
            "available": True,
            "models": models,
            "default_model": default_model_name(models) if models else None,
            "cache_dir": str(cache),
            "cached": cached,
        }
        try:
            import ctranslate2

            whisper["cuda_devices"] = int(ctranslate2.get_cuda_device_count())
        except Exception:
            whisper["cuda_devices"] = None
    except Exception as exc:
        whisper["error"] = str(exc)
    try:
        running = session.manager._running_browsers()
    except Exception:
        running = []
    developer_json = resolve_developer_json()
    report = {
        "python": {"version": sys.version.split()[0], "executable": sys.executable},
        "app": {"dir": str(app_dir()), "frozen": is_frozen()},
        "paths": {
            "settings": str(user_settings_path()),
            "developer_json": str(developer_json) if developer_json else None,
        },
        "yt_dlp": get_yt_dlp_version(),
        "yt_dlp_ejs": _version_of("yt-dlp-ejs"),
        "ffmpeg": {"path": session.ffmpeg_path, "version": _ffmpeg_version(session.ffmpeg_path)},
        "ffprobe": {"path": session.ffprobe_path},
        "js_runtime": {"name": js_runtime, "path": js_path},
        "whisper": whisper,
        "selenium": _version_of("selenium"),
        "cookies": {
            "browser": session.cookies_browser,
            "file": session.cookies_path,
            "source": session.cookies_source,
            "running_browsers": running,
        },
    }
    run.report = report
    if not session.ffmpeg_path:
        run.warn_once("ffmpeg_missing", "ffmpeg is not on PATH: no merged best quality, MP3, sections or direct media.")
    if not session.ffprobe_path:
        run.warn_once("ffprobe_missing", "ffprobe is not on PATH: media facts are omitted from results.")
    if not js_runtime:
        run.warn_once("js_runtime_missing", "No deno/node/bun on PATH: some YouTube formats will fail.")
    if not report["yt_dlp_ejs"]:
        run.warn_once("yt_dlp_ejs_missing", "yt-dlp-ejs is not installed; challenge solving relies on remote scripts.")
    if not whisper["available"]:
        run.warn_once("whisper_unavailable", "Faster Whisper is unavailable; `transcribe` will not work.")


__all__ = [
    "FORMAT_PRESETS",
    "Run",
    "choose_track",
    "cmd_doctor",
    "cmd_download",
    "cmd_info",
    "cmd_transcribe",
    "cmd_transcript",
    "parse_items",
    "resolve_format",
]
