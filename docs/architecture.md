# Link2Vid architecture

Current layout and data flow for the desktop app. For media discovery details (Selenium collapse, filenames), see [media-discovery-pipeline.md](./media-discovery-pipeline.md).

## Design decisions

- **Python engine, web frontend** — yt-dlp, Faster Whisper, and Selenium are Python libraries, so the engine stays in Python. The UI is HTML/CSS/JS (Preact + htm, vendored, no build step) hosted in a native window by [pywebview](https://pywebview.flowrl.com/) (WebView2 on Windows). This replaced CustomTkinter, which could not deliver a modern look.
- **Three layers** — `link2vid/core/` (fetch/download/transcribe, UI-agnostic), `link2vid/app/` (toolkit-neutral orchestration + view models), `link2vid/ui/` (pywebview host + `web/` frontend). See [ui-frontend.md](./ui-frontend.md) for the frontend.
- **Python owns state** — view models publish JSON events; the page renders them and sends intents back. No business rules live in JavaScript.
- **Thread safety** — one controller dispatcher thread owns all session state; network, downloads, transcription, and Selenium run on workers that hop back through `ui_queue`.

## Entry point

- `video_downloader.py` — `main()` parses `--smoke` / `--devtools`, calls `bootstrap_runtime()`, then lazy-imports `link2vid.ui.webview_app.run` or runs headless import smoke.
- `link2vid/core/runtime.py` — frozen/dev `app_dir`, `developer.json` search order, optional `<app_dir>/bin` PATH prepend (entry-only; not imported from other core modules).
- `link2vid/app/controller.py` — `AppController`: fetch/download/transcript/Whisper/batch orchestration, dialogs, diagnostics, the `ui_queue` dispatcher.
- `link2vid/app/viewmodels.py` — `CardModel`, `BatchBarModel`, `FooterModel`, `UrlModel`, `LogModel`: state objects that replaced the old widgets and publish events.
- `link2vid/app/view.py` — `ViewPort` protocol (events, dialogs, toasts, clipboard, file pickers) and a headless `NullView`.
- `link2vid/app/desktop.py` — clipboard text, open/reveal files.
- `link2vid/ui/webview_app.py` — `WebViewPresenter` (batches events into the page), `Bridge` (JS API), window setup and Windows title-bar theming.
- `link2vid/ui/web/` — `index.html`, `styles.css`, `js/*.js`, `vendor/preact-htm.module.js`.

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
| `link2vid/core/udio.py` | Public Udio `/songs/{id}` metadata → direct MP3 entries (no login) |
| `link2vid/core/gdcvault.py` | GDC Vault talks → Blazestreaming HLS entry; HTTP login only for members-only pages |
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
       1. Udio public song API (udio.com `/songs/{id}` only; never Selenium)
       1b. GDC Vault (gdcvault.com; never Selenium): resolve the Blazestreaming
           player; members-only pages → NeedsLogin → UI prompts → HTTP form login,
           re-resolve, logout. No player → yt-dlp with credentials (older talks).
       2. yt-dlp extract_info
       3. configured embedded-page OpenGraph/JSON scrape
       4. scan_direct_media_entries (HTTP HTML)
       5. scan_direct_m3u8
       6. NeedsSelenium → UI prompts → selenium_fetch_media_entries
  → controller publishes results + CardModel pages (40 at a time; the page requests more on scroll)
```

Auth routing: when yt-dlp fails with a cookie/auth signal, `VideoFetcher` returns `NeedsSelenium` if the exception chain shows a login redirect (`Unsupported URL` to `/login`, `/users/login`, `/sign_in`, or `/sign-in`), if username+password were already supplied, or if the URL matches a configured `developer.json` credential domain. Unconfigured generic cookie failures still return `NeedsCookies` so the footer browser picker and automatic browser-cookie lookup remain the path for ordinary sites. Inspect the full exception chain — the final `CookiesRequiredError` message alone often loses the login redirect.

Fetch runs off the dispatcher thread via `ThreadPoolExecutor`. Results and logs reach view models through `ui_queue`.

## Download flow

Per card, user picks a format (Best A+V / Best video / Audio MP3) or Transcript. **Audio (MP3)** prefers a muxed file that already has audio (`best[acodec!=none]/best/bestaudio`) and extracts a playable MP3. That matches YouTube cases where **Best (A+V)** works but a dedicated `bestaudio` URL returns HTTP 403. Direct-media/HLS entries honor the same choice via ffmpeg (`-vn`). Whisper temporary `bestaudio` acquisition is unchanged and does not convert to MP3.

- **yt-dlp path** — standard sites, playlists; `DownloadManager` with progress hooks. **Audio (MP3)** adds an `FFmpegExtractAudio` postprocessor (`extract_audio=True`); Whisper temporary `bestaudio` downloads leave this off.
- **Direct media path** — entries with `_ffmpeg_headers` use `download_with_ffmpeg`; **Audio (MP3)** writes `.mp3` with `-vn` instead of copying video. Progress uses `ffmpeg_progress_display` for coherent elapsed/total display. On Windows, ffmpeg/ffprobe subprocesses launch with `CREATE_NO_WINDOW` so a closable console cannot abort an in-flight download.
- **Transcript path** — caption/subtitle files only; no media mux.

Output names use `sanitize_filename` + `unique_output_path` (collision suffix, no silent overwrite).

## Batch selection and downloads

`AppController` owns fetched-entry selection and per-entry format/transcript choices, keyed by entry index for one fetch session. Card checkboxes and menus in the page are input/display only. This keeps selection authoritative across incremental rendering without constructing every card; an unrendered entry uses the same session-default format as a newly rendered card.

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
          (one browser-cookie prompt when no preferred browser is saved)
  → WhisperService
       1. load/reuse the selected model from the shared model cache
       2. use CUDA when CTranslate2 reports a device; otherwise select CPU once
       3. stream segments into collision-safe .txt and .srt outputs
  → release temporary source lease
  → request-scoped queue events update card state and sanitized diagnostics
```

Model weights live at `%LOCALAPPDATA%\Link2Vid\models` (fallback `~/.link2vid/models`) in both source and frozen modes. They are downloaded on demand and are never bundled. Whisper work is serialized inside the service to bound native model memory; stale queue events cannot clear or overwrite a newer card run.

## Threading and UI safety

- `AppController.run_forever` runs on a dedicated dispatcher thread (`main_thread_id`); only that thread mutates view models and session maps.
- Background work: fetch, downloads, Whisper audio acquisition/transcription, Selenium. Thumbnails load in the WebView directly (`referrerpolicy="no-referrer"`).
- Pattern: workers enqueue `(action, payload)` tuples; `process_ui_queue` drains them on the dispatcher thread. Page intents arrive on pywebview worker threads and are `post`ed onto the same queue.
- Dialogs (`_ask`) block the calling thread until the page answers. On the dispatcher thread the queue keeps draining while a dialog is open, so progress and logs stay live.
- `WebViewPresenter` coalesces events (latest `card` per key, latest `progress`/`batch`/`settings`) and flushes at most ~30 times per second via `window.L2V.receive(...)`.
- Batch workers iterate frozen `BatchItem` values and never read live entry/selection maps.

## Local configuration

- `developer.json` (gitignored) — optional `use_defaults`, `cookies_browser`, per-domain login credentials.
- `%APPDATA%/Link2Vid/settings.json` — persisted cookie-browser and cookies.txt preferences.
- `%LOCALAPPDATA%/Link2Vid/webview` — WebView2 profile (page storage only).
- **Dev mode:** resolved from cwd, then entry script directory, then `%APPDATA%/Link2Vid/`.
- **Frozen (packaged) mode:** resolved from directory containing `Link2Vid.exe`, then `%APPDATA%/Link2Vid/`. Build may copy repo-root `developer.json` into `release/Link2Vid/`.
- Credentials apply only when the fetch URL matches a configured domain (including subdomains).

## Tests

Pure logic lives in `link2vid/core/` and is covered by `tests/`. Run from repo root:

```bash
python -m pytest tests/
```

Controller and view-model behavior is covered headlessly (`tests/test_whisper_workflow.py`, `tests/test_batch_workflow.py`, `tests/test_viewmodels.py`, `tests/test_webview_bridge.py`); no WebView is needed to run the suite.

Packaging contract tests: `tests/test_runtime.py`, `tests/test_packaging_contract.py`.

See [verification-checklist.md](./verification-checklist.md) for manual regression scenarios (including packaged build).
