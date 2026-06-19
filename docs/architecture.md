# Link2Vid architecture

Current layout and data flow for the desktop app. For media discovery details (Selenium collapse, filenames), see [media-discovery-pipeline.md](./media-discovery-pipeline.md).

## Design decisions

- **CustomTkinter** — kept the existing UI framework; refactored incrementally into components rather than rewriting.
- **Core/UI split** — fetch and download logic live in `link2vid/core/`; the UI orchestrates and owns all dialogs and widget updates.
- **Thread safety** — network, downloads, thumbnails, and Selenium run off the UI thread; workers communicate via `ui_queue` drained on the main thread.

## Entry point

- `video_downloader.py` — `main()` parses `--smoke`, calls `bootstrap_runtime()`, then lazy-imports the UI or runs headless import smoke.
- `link2vid/core/runtime.py` — frozen/dev `app_dir`, `developer.json` search order, optional `<app_dir>/bin` PATH prepend (entry-only; not imported from other core modules).
- `link2vid/ui/main_window.py` — main window, fetch/download orchestration, UI event queue.
- `link2vid/ui/components/` — `VideoCard`, `LogDrawer`, `FooterBar`.
- `link2vid/ui/thumbnail_loader.py` — background thumbnail fetch/resize.

Windows portable builds: `build_windows.bat` → `release/Link2Vid/`; launch via `Link2Vid.bat`. See [windows-packaging.md](./windows-packaging.md).

## Core modules

| Module | Role |
|---|---|
| `link2vid/core/runtime.py` | Frozen detection, app directory, `developer.json` resolution, sidecar `bin/` PATH bootstrap |
| `link2vid/core/fetcher.py` | `VideoFetcher.fetch` — yt-dlp first, then configured embedded-page scrape, direct media scan, HLS scan, or `NeedsSelenium` |
| `link2vid/core/downloader.py` | `DownloadManager` — yt-dlp media/transcript downloads, cookie/browser retry, progress hooks |
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

## Threading and UI safety

- Tk/CTk widgets are updated only on the main thread.
- Background work: fetch, downloads, thumbnails, Selenium.
- Pattern: workers enqueue `(action, payload)` tuples; `process_ui_queue` drains them on the UI thread.

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
