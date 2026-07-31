# Link2Vid architecture

Current layout and data flow for the desktop app. For media discovery details (Selenium collapse, filenames), see [media-discovery-pipeline.md](./media-discovery-pipeline.md).

## Design decisions

- **CustomTkinter** — kept the existing UI framework; refactored incrementally into components rather than rewriting.
- **Core/UI split** — fetch and download logic live in `link2vid/core/`; the UI orchestrates and owns all dialogs and widget updates.
- **Thread safety** — network, downloads, transcription, thumbnails, and Selenium run off the UI thread; workers communicate via `ui_queue` drained on the main thread.

## Entry point

- `video_downloader.py` — `main()` parses `--smoke`, calls `bootstrap_runtime()`, then lazy-imports the UI or runs headless import smoke.
- `link2vid/core/runtime.py` — frozen/dev `app_dir`, `developer.json` search order, optional `<app_dir>/bin` PATH prepend (entry-only; not imported from other core modules).
- `link2vid/ui/main_window.py` — main window, fetch/download orchestration, UI event queue.
- `link2vid/ui/components/` — `VideoCard`, `BatchBar`, `LogDrawer`, `FooterBar`.
- `link2vid/ui/thumbnail_loader.py` — background thumbnail fetch/resize.

Windows portable builds: `build_windows.bat` → `release/Link2Vid/`; launch via `Link2Vid.bat`. See [windows-packaging.md](./windows-packaging.md).

## Core modules

| Module | Role |
|---|---|
| `link2vid/core/runtime.py` | Frozen detection, app directory, `developer.json` resolution, sidecar `bin/` PATH bootstrap |
| `link2vid/core/fetcher.py` | `VideoFetcher.fetch` — yt-dlp first, then configured embedded-page scrape, direct media scan, HLS scan, or `NeedsSelenium` |
| `link2vid/core/downloader.py` | `DownloadManager` — yt-dlp media/transcript downloads, cookie/browser retry, progress hooks |
| `link2vid/core/audio_source.py` | `AudioSourceResolver` — exact verified-media reuse or action-owned temporary `bestaudio` acquisition and cleanup |
| `link2vid/core/whisper_transcription.py` | Faster Whisper contracts, model validation/cache, serialized CUDA-or-CPU transcription, paired text/SRT output |
| `link2vid/core/extractors.py` | Embedded-page scrape, HTTP direct media scan, HLS detection, `build_media_entries` |
| `link2vid/core/selenium_fallback.py` | Browser login, `discover_media_urls`, `collapse_selenium_media_candidates`, `selenium_fetch_media_entries` |
| `link2vid/core/helpers.py` | URL normalization, filename sanitization, FFmpeg helper, format options |
| `link2vid/core/diagnostics.py` | `build_diagnostics` for Copy Diagnostics |
| `link2vid/core/error_classification.py` | Error reason codes and user guidance |
| `link2vid/core/dev_defaults.py` | Optional local `developer.json` credentials per domain |

## Fetch flow

```
User URL
  → normalize_url
  → VideoFetcher.fetch (background thread)
       1. yt-dlp extract_info
       2. configured embedded-page OpenGraph/JSON scrape
       3. scan_direct_media_entries (HTTP HTML)
       4. scan_direct_m3u8
       5. NeedsSelenium → UI prompts → selenium_fetch_media_entries
  → UI renders VideoCard list (batched with Load more)
```

Fetch runs off the UI thread via `ThreadPoolExecutor`. Results and logs reach widgets through `ui_queue` + `root.after`.

## Download flow

Per card, user picks a format (Best A+V / Best video / Best audio) or Transcript.

- **yt-dlp path** — standard sites, playlists; `DownloadManager` with progress hooks.
- **Direct media path** — entries with `_ffmpeg_headers` use `download_with_ffmpeg`; progress uses `ffmpeg_progress_display` for coherent elapsed/total display.
- **Transcript path** — caption/subtitle files only; no media mux.

Output names use `sanitize_filename` + `unique_output_path` (collision suffix, no silent overwrite).

## Batch selection and downloads

`VideoDownloaderApp` owns fetched-entry selection and per-entry format/transcript choices, keyed by entry index for one fetch session. `VideoCard` checkboxes and menus are input/display only. This keeps selection authoritative across **Load more** without constructing every card; an unrendered entry uses the same session-default format as a newly rendered card.

At batch start the app freezes the selected entries' resolved URLs and choices. One daemon worker then calls the existing `download_video` or `download_transcript` runner sequentially, hopping to the UI thread for per-item pre-flight and widget state. Failures are counted and isolated, and **Stop** takes effect between items. Batch provider transcripts never invoke Whisper.

`core.helpers.entry_watch_url` is the canonical pure resolver for per-entry watch URLs. Multi-entry sessions never fall back to the playlist/channel URL in the address bar; an unresolvable entry is skipped instead.

## Local Whisper flow

Each card exposes a model selector and a Whisper action independently of provider-caption availability.

```
Whisper click
  → freeze request, selected model, card epoch, and current VerifiedMedia
  → AudioSourceResolver
       1. revalidate and reuse the exact previously downloaded file, or
       2. download bestaudio into an action-owned temporary workspace
          (one cookies.txt retry when required)
  → WhisperService
       1. load/reuse the selected model from the shared model cache
       2. use CUDA when CTranslate2 reports a device; otherwise select CPU once
       3. stream segments into collision-safe .txt and .srt outputs
  → release temporary source lease
  → request-scoped queue events update card state and sanitized diagnostics
```

Model weights live at `%LOCALAPPDATA%\Link2Vid\models` (fallback `~/.link2vid/models`) in both source and frozen modes. They are downloaded on demand and are never bundled. Whisper work is serialized inside the service to bound native model memory; stale queue events cannot clear or overwrite a newer card run.

## Threading and UI safety

- Tk/CTk widgets are updated only on the main thread.
- Background work: fetch, downloads, Whisper audio acquisition/transcription, thumbnails, Selenium.
- Pattern: workers enqueue `(action, payload)` tuples; `process_ui_queue` drains them on the UI thread.
- Batch workers iterate frozen `BatchItem` values and never read live entry/selection maps.

## Local configuration

- `developer.json` (gitignored) — optional `use_defaults`, `cookies_browser`, per-domain login credentials.
- **Dev mode:** resolved from cwd, then entry script directory, then `%APPDATA%/Link2Vid/`.
- **Frozen (packaged) mode:** resolved from directory containing `Link2Vid.exe`, then `%APPDATA%/Link2Vid/`. Build may copy repo-root `developer.json` into `release/Link2Vid/`.
- Credentials apply only when the fetch URL matches a configured domain (including subdomains).

## Tests

Pure logic lives in `link2vid/core/` and is covered by `tests/`. Run from repo root:

```bash
python -m pytest tests/
```

Packaging contract tests: `tests/test_runtime.py`, `tests/test_packaging_contract.py`.

See [verification-checklist.md](./verification-checklist.md) for manual regression scenarios (including packaged build).
