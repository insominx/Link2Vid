# Headless CLI (agents and scripts)

Doggybag's downloader runs without its window. Use the CLI from scripts, other projects, and coding agents.
Every command is non-interactive. It prints one JSON result on stdout and sends logs and progress to stderr.
It never prompts. When it needs something from you, it exits with a code and tells you exactly which flags to add.

The CLI runs from a source checkout. The packaged `Doggybag.exe` is a windowed build with no console,
so it cannot print results.

## Invocation

```bat
doggybag.bat <command> [options]
python video_downloader.py <command> [options]
```

The command comes first. `doggybag.bat` picks `.venv\Scripts\python.exe` when present and otherwise uses
system Python (3.10+ with `requirements.txt` installed). It does not change directory, so the current
directory is the default output folder. With no arguments, `video_downloader.py` still launches the GUI.

Use the CLI, not `doggybag.core` internals. Module paths and private methods change; for example the
`link2vid` package became `doggybag`. The command and its JSON output are the supported interface.

## Commands

| Command | What it does |
|---|---|
| `info URL...` | Lists each entry: title, duration, chapters, caption tracks, and formats. `--formats` adds format tables to multi-entry results; a single entry always has them. |
| `download URL...` | Downloads at the best source resolution and frame rate by default. `--audio` (MP3), `--video-only`, `--format ID` (`best`, `video-only`, `audio`, or any yt-dlp selector). |
| `transcript URL...` | Saves the site's own subtitles or captions. `--list`, `--lang en`, `--source subtitles\|auto`. |
| `transcribe URL\|FILE...` | Transcribes locally with Faster Whisper into `.txt` and `.srt`. `--model tiny` is fastest; the default is `large-v3`. Local files are used in place. |
| `doctor` | Offline environment report: ffmpeg/ffprobe, JS runtime, yt-dlp, Whisper models and GPU, cookie settings. |
| `schema` | Machine-readable contract: every command, flag, exit code and error code. |

Shared flags (they go after the command):

| Flag | Meaning |
|---|---|
| `--out DIR` | Output folder. Default: the current directory; created if missing. |
| `--items SPEC` | Which entries of a playlist or channel to use: `all`, `3`, `1,4,6-9` (1-based). |
| `--flat` | List playlists without resolving every entry. Much faster; tracks and formats are omitted. |
| `--cookies-browser brave\|chrome\|edge\|firefox` | Send that browser's cookies from the first attempt. |
| `--cookies-file PATH` | Send a Netscape `cookies.txt`. |
| `--username`, `--password` | Site credentials. Prefer env `DOGGYBAG_USERNAME` / `DOGGYBAG_PASSWORD`. Never echoed. |
| `--allow-browser` | Allow the Selenium fallback. It opens a **visible Chrome window**, so ask the user first. |
| `--text` | Human-readable summary instead of JSON. |
| `--stream` | NDJSON events on stdout (`fetch`, `item_start`, `progress`, `phase`, `item_done`); the last line is the `result`. |
| `--quiet` / `--log-file PATH` | Silence stderr / also append logs to a file. |

Download-only flags:

- `--section START-END`: cut a clip, e.g. `1:23-2:45`, `90-` or `-0:30`. Repeatable.
- `--section-mode exact|copy`: how clips are cut.
- `--keep-full`: also keep the full source next to the clips.
- `--name STEM`: output file name, for one item only.
- `--format-fallback best|fail`: what to do when the format is unavailable.
- `--if-no-ffmpeg fallback|continue|fail`: what to do when a merge format needs ffmpeg and it is missing.

## Examples

```bat
doggybag.bat doctor --text
doggybag.bat info "https://www.youtube.com/watch?v=VIDEO"
doggybag.bat download "https://www.youtube.com/watch?v=VIDEO" --out D:\clips
doggybag.bat download "https://www.youtube.com/watch?v=VIDEO" --section 0:10-0:25 --section 2:00-2:30
doggybag.bat info "https://www.youtube.com/@channel/videos" --flat
doggybag.bat download "https://www.youtube.com/@channel/videos" --flat --items 1-3
doggybag.bat transcript "https://www.youtube.com/watch?v=VIDEO" --lang en
doggybag.bat transcribe "D:\clips\talk.mp4" --model tiny
```

## Result

```json
{
  "schema": "doggybag.cli/1", "command": "download", "status": "ok", "ok": true, "exit_code": 0,
  "out_dir": "D:\\clips", "inputs": ["https://www.youtube.com/watch?v=VIDEO"],
  "fetches": [{"url": "...", "source": "yt-dlp", "entries": 1, "collection": null, "error": null}],
  "items": [{
    "index": 1, "source_url": "...", "url": "...", "title": "Example talk",
    "action": "download", "route": "yt-dlp", "status": "ok",
    "format": {"requested": "bestvideo+bestaudio/best", "effective": "bestvideo+bestaudio/best",
               "label": "Best (A+V)", "extract_audio": false, "substituted": false},
    "player_client": "web_embedded", "cookies": {"mode": "none", "browser": null, "source": "none"},
    "files": [{"kind": "clip", "path": "D:\\clips\\Example talk.clip-00.00.10-00.00.25.mp4", "size": 7835951,
               "section": {"start": 10.0, "end": 25.0, "mode": "exact"},
               "duration": 15.0, "width": 1920, "height": 1080, "fps": 30.0, "vcodec": "h264", "acodec": "aac"}],
    "warnings": [{"code": "player_client_fallback", "message": "Succeeded with YouTube player client 'web_embedded'."}],
    "error": null
  }],
  "warnings": [], "error": null, "report": null,
  "tools": {"yt_dlp": "2026.08.19", "ffmpeg": "C:\\...\\ffmpeg.EXE", "js_runtime": "node"},
  "elapsed_seconds": 12.17
}
```

- Item `status`: `ok`, `already_exists`, `failed`, `skipped`.
- Envelope `status`: `ok`, `partial`, `failed`, `needs_action`, `interrupted` (`doctor` adds `degraded`).
- File `kind`: `media`, `clip`, `transcript`, `whisper_text`, `whisper_srt`.
- Media facts (`duration`, `width`, `height`, `fps`, codecs) come from ffprobe and are omitted without it.
- `info` never includes request headers, signed media URLs, or cookies. Direct-media entries report `url: null`.

## Exit codes and errors

| Exit | Meaning |
|---|---|
| 0 | Every requested item succeeded or already existed |
| 1 | Failed (network, extractor, ffmpeg, verification…); see `error.code` |
| 2 | Usage: bad flags, `--section`, `--items`, file or Whisper model |
| 3 | Partial: some items succeeded, some failed |
| 4 | Needs action: re-run with the flags in `error.next_steps` |
| 5 | Nothing to get: no media, transcript or speech |
| 6 | Dependency missing: ffmpeg or Faster Whisper |
| 130 | Interrupted (Ctrl+C) |

When every item fails for different reasons, the exit code follows this priority: 4, then 2, then 6, then 5, then 1.

Errors look like
`{"code", "message", "type", "reason", "hint", "url", "stage", "next_steps": [{"add_flags": [...], "note"}]}`.
Codes come from the exception type first and only fall back to message text. The main ones:

| Code | Exit | What to do |
|---|---|---|
| `cookies_required` | 4 | Add `--cookies-browser <browser>` (close that browser first) or `--cookies-file` |
| `credentials_required` | 4 | Set `DOGGYBAG_USERNAME` / `DOGGYBAG_PASSWORD` |
| `browser_automation_required` | 4 | Ask the user, then add `--allow-browser` |
| `items_required` | 4 | Add `--items`; `fetches[].listing` shows the entries |
| `format_unavailable` | 4 | Use `--format-fallback best` or pick an id from `info --formats` |
| `no_media_found`, `no_transcript`, `no_speech` | 5 | Try `transcribe` instead of `transcript`, or another URL |
| `ffmpeg_missing`, `whisper_unavailable` | 6 | Install the dependency; check with `doctor` |
| `network`, `js_runtime`, `extractor`, `media_forbidden`, `download_stalled` | 1 | Wait and retry, or update yt-dlp / yt-dlp-ejs |

`schema` lists every code.

## Defaults and substitutions

- **Quality:** best video plus best audio, merged at the source resolution and frame rate. Nothing is capped.
- **Unavailable format:** retried with the best available format (merged when ffmpeg is present). Reported as a
  `format_fallback` warning with `format.substituted: true`. Use `--format-fallback fail` to stop instead.
- **Missing ffmpeg:** merge formats fall back to a single file (`ffmpeg_missing_fallback` warning).
  Audio is saved in its original format instead of MP3. `--section` requires ffmpeg.
- **YouTube throttling:** a media 403 *or* a download that crawls below 64 KiB/s for 30 s is retried on the
  `web_embedded`, `tv_embedded` and `mweb` player clients. The result reports the client in `player_client`.
- **Playlists:** a URL with several entries needs `--items`, so an agent never downloads a whole channel by accident.
  Unlike GUI batches, direct-media entries are allowed in multi-item runs.

## Sections

`--section` downloads the full source into a hidden `.doggybag-section-*` folder inside `--out`, cuts each clip
locally, then removes the folder. Downloading YouTube ranges directly through ffmpeg is unreliable, so cutting locally
is the dependable route.

- **exact** (default): re-encodes (H.264 CRF 18 + AAC, or MP3 for audio) for frame-accurate edges.
- **copy**: lossless and fast, but each clip starts on the previous keyframe.

Clips are named `<title>.clip-HH.MM.SS-HH.MM.SS.<ext>` and never overwrite an existing file. An end past the media
length is clamped, with a `section_clamped` warning.

## Auth

Saved preferences (`%APPDATA%\Doggybag\settings.json`, `developer.json`) are read but never written. A saved cookie
browser is used only after a site asks for sign-in, as in a fresh GUI session. `--cookies-browser` forces cookies
from the first attempt. Each item reports `cookies.source` as `flag`, `settings`, `developer.json` or `none`.

## Whisper

The first use of a model downloads it into `%LOCALAPPDATA%\Doggybag\models`. Transcription uses CUDA when available,
otherwise CPU. A local file is transcribed in place with no download. Pass a previous `download` result's
`files[0].path` to reuse it.

## Guarantees and limits

- Never prompts. Never reads stdin or the clipboard. Never writes `settings.json` or the GUI debug log.
- Selenium runs only with `--allow-browser`.
- Ctrl+C exits 130 with an `interrupted` result and stops a running ffmpeg. yt-dlp `.part` files, and Whisper
  temporary files from an interrupted transcription, can be left behind.

## Troubleshooting

Run `doctor` first. For cookie, JavaScript runtime and ffmpeg problems, see
[cookie-js-troubleshooting.md](./cookie-js-troubleshooting.md).
