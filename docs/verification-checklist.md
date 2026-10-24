# Verification checklist (manual)

Canonical regression list. Run before releases or after changes to fetch, download, or UI flow.

Automated baseline: `python -m pytest tests/`

## Standard media pages

1. **Public, non-age video** — formats load, download completes, audio present.
2. **Age-restricted video** — choose the logged-in browser from **Cookies** in the status bar (and close it if needed); formats load, download completes.
3. **Music video (common throttling)** — download completes; no persistent 403/429.
4. **Playlist URL** — multiple cards appear; at least one item downloads.
5. **Public Udio song** (`udio.com/songs/{id}`) — one card appears without a username/password prompt; **Best (A+V)** or **Audio (MP3)** saves the track. Creator/library pages should explain that a public song URL is required.

## Browser-cookie retry

1. **Auth-required video** — app retries browser cookies automatically.
2. **Browser cookie failure (DPAPI / locked profile)** — if no browser is saved, the app asks which browser to use and remembers it. If a browser is already chosen, it tells you to close that browser instead of asking for `cookies.txt`.

## Format selection

- **Best (A+V)** — playable file with audio.
- **Best video** — video-only file.
- **Audio (MP3)** — playable MP3; no video stream. On YouTube, this should succeed when **Best (A+V)** does, even if a dedicated audio URL 403s. Direct HLS with this format also writes MP3.

## Transcript

- Transcript action saves caption/subtitle files only (no media file).
- No tracks available — card shows clear failed/unavailable state; logs explain why.
- Auth/consent gating — cookie retry and `cookies.txt` prompt still work.

## Batch selection and batch actions

1. Fetch a playlist/channel with more than 40 entries. Confirm every entry is selected by default and the results header count (`N of N`) matches the fetched count before scrolling to the end.
2. Clear the header checkbox (select none), select two visible cards, then scroll until more cards load. Newly rendered cards remain unchecked and the count stays at two. The header checkbox restores the full selection.
3. Select at least three items, including one not yet rendered, and change one rendered card's format. **Download N** creates one separate media result per item, uses the changed format for that card and the session default for the unrendered item, and creates no combined artifact.
4. Run **Save captions** over the same items. Confirm separate provider-caption files, no media files, and no Whisper artifacts.
5. Include one failing item. Later items still run and the final completed/failed/skipped summary is accurate.
6. Press **Stop** during an item. The in-flight item finishes and every remaining item is skipped; the header returns to idle with the run summary.
7. Trigger cookies, unavailable-format, or missing-ffmpeg fallback on several items. Each question appears at most once during that run and appears normally again for a later per-card action.

## Local Whisper

- Model list probes without downloading weights; a missing/broken native dependency disables only Whisper controls.
- First use downloads the selected model to `%LOCALAPPDATA%\Doggybag\models`; a later offline run succeeds from that cache.
- A successful media download followed by Whisper reuses that exact unchanged file.
- Whisper without a prior download acquires temporary `bestaudio`; the action-owned temporary workspace is removed after success and failure.
- Auth-required temporary audio gets one browser-cookie prompt/retry when no preferred browser is saved.
- Successful transcription writes non-empty, matching `<title>.whisper.<language>.txt` and `.srt` files; repeated runs use collision suffixes.
- With compatible NVIDIA CUDA, diagnostics report `effective_device: cuda`. With no CUDA device reported by CTranslate2, the run selects CPU and records fallback. CUDA model-loading failures are surfaced rather than retried.
- The card remains responsive, blocks overlapping actions, and ignores stale completion/progress events from an older request.

## Dependencies

- ffmpeg missing — warning before merge-required downloads.
- ffmpeg present — no warning.
- JS-runtime-required page without JS runtime — startup log warns; fetch/download may fail until deno/node/bun is installed.

## Diagnostics

Copy Diagnostics includes at minimum:

- URL, action kind (media, provider transcript, or Whisper), selected format
- yt-dlp version, ffmpeg path, JS runtime detection
- Cookies mode and browser (when used)
- Last error and classified reason
- Recent log tail
- For the latest Whisper run: phase, model, cache-root category, effective device, fallback flag, source kind, and sanitized error reason (no local paths, secrets, or traceback)

## UI stability

- Fetch does not freeze the window; skeleton cards show the latest log line while fetching.
- Card progress and the thin top progress line update smoothly; indeterminate phases (Whisper model load, transcription) animate without a fake percentage.
- Completed cards offer **Open** and **Show in folder** for the saved file; failed cards offer **Retry** and **Details**.
- **Activity** (status bar, `Ctrl+J`) slides up with live logs; the badge counts unseen errors.
- Large playlists render 40 cards at a time and load more on scroll; scrolling stays responsive.
- Thumbnails fade in; missing or blocked thumbnails show a tinted placeholder.
- `Ctrl+V` outside a text field, or dropping a link on the window, fetches it. Focusing the window with a new link on the clipboard offers **Fetch it**.
- Light and dark mode follow Windows; the title bar matches the page background.
- Dialogs (cookie browser, sign-in, format fallback, ffmpeg missing) are keyboard operable: `Esc` cancels, `Enter` confirms.

## Authenticated / multi-item external pages

- One selectable item per logical media asset (not one per scraped URL variant).
- Download inputs are direct media/manifest URLs, not embed or player page URLs.
- Logs show raw candidate count vs collapsed logical count (sanitized output OK).
- At least one live download completes with a title-derived filename.
- Re-download of the same title uses a collision suffix; no silent overwrite.

## Fallback paths (spot-check)

- **Embedded-page video** — direct media URL found when present on page.
- **Direct HLS** — m3u8 detected; FFmpeg download succeeds.
- **Selenium fallback** — UI stays responsive while browser automation runs off the dispatcher thread.

## Headless CLI

Run from a scratch folder with `doggybag.bat` (see [headless-usage.md](./headless-usage.md)); check `%ERRORLEVEL%` after each.

- `doctor --text` and `schema` succeed (exit 0).
- `info <public video>` lists formats, chapters and caption tracks; the output has no `Cookie` or `http_headers`.
- `download <public video> --section 0:10-0:25` writes a 15 s clip with ffprobe facts and leaves no `.doggybag-section-*` folder.
- `download <video> --format 999` succeeds with a `format_fallback` warning; with `--format-fallback fail` it exits 4.
- `download <channel>/videos --flat` without `--items` exits 4 (`items_required`); `--items 1` works.
- `transcript <video> --list`, then `--lang en` saves real subtitles when the site has them.
- `transcribe <clip> --model tiny` writes `.txt` and `.srt` without downloading; a URL leaves no `doggybag-whisper-audio-*` folder.
- `--stream`: every stdout line parses as JSON and the last is `result`.
- Ctrl+C during a direct-HLS download exits 130 and leaves no `ffmpeg` process.
- `settings.json` and `~/Downloads/doggybag-debug.log` are unchanged by CLI runs.

## Packaged build (Windows exe)

After `build_windows.bat`, the `release/Doggybag/` folder opens automatically. Start via `Doggybag.bat` (smoke also runs during build). Optional: `packaging\smoke_frozen.bat`. Then spot-check using the scenarios above. See [windows-packaging.md](./windows-packaging.md) for layout.

Additional frozen-only checks:

- The window opens with the Doggybag icon and the web UI (no blank page); `Doggybag.exe --smoke` also verifies the bundled `doggybag/ui/web` assets.

- Startup log shows `runtime: frozen=True ...` before the `UI runtime:` line.
- Copy Diagnostics reports ffmpeg under `\bin\` when `bin/ffmpeg.exe` is staged.
- `developer.json` beside `Doggybag.exe` (or in `%APPDATA%\Doggybag\`) prefills `default_url` when configured; bootstrap log shows resolved path.
- One HLS or Best (A+V) download succeeds with staged ffmpeg and a JS runtime (system or `bin/deno.exe`).
- `Doggybag.exe --smoke` imports the Faster Whisper native/dynamic dependency closure.
- A frozen CPU transcription completes and writes `.txt` plus `.srt`; model weights remain in the shared external cache rather than `_internal/`.
