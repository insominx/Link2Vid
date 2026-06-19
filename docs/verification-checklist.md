# Verification checklist (manual)

Canonical regression list. Run before releases or after changes to fetch, download, or UI flow.

Automated baseline: `python -m pytest tests/`

## Standard media pages

1. **Public, non-age video** — formats load, download completes, audio present.
2. **Age-restricted video** — provide `cookies.txt`; formats load, download completes.
3. **Music video (common throttling)** — download completes; no persistent 403/429.
4. **Playlist URL** — multiple cards appear; at least one item downloads.

## Browser-cookie retry

1. **Auth-required video** — app retries browser cookies automatically.
2. **Browser cookie failure (DPAPI / locked profile)** — app prompts for `cookies.txt`; download completes after selection.

## Format selection

- **Best (A+V)** — playable file with audio.
- **Best video** — video-only file.
- **Best audio** — audio-only file.

## Transcript

- Transcript action saves caption/subtitle files only (no media file).
- No tracks available — card shows clear failed/unavailable state; logs explain why.
- Auth/consent gating — cookie retry and `cookies.txt` prompt still work.

## Dependencies

- ffmpeg missing — warning before merge-required downloads.
- ffmpeg present — no warning.
- JS-runtime-required page without JS runtime — startup log warns; fetch/download may fail until deno/node/bun is installed.

## Diagnostics

Copy Diagnostics includes at minimum:

- URL, action kind (media vs transcript), selected format
- yt-dlp version, ffmpeg path, JS runtime detection
- Cookies mode and browser (when used)
- Last error and classified reason
- Recent log tail

## UI stability

- Fetch does not freeze the window.
- Progress bar updates without crash; elapsed/total stay coherent with known duration.
- Logs update during downloads.
- Log drawer toggles cleanly; collapsed by default.
- Large playlists render in batches (**Load more**); scrolling stays responsive.
- Thumbnails load progressively without stutter.

## Authenticated / multi-item external pages

- One selectable item per logical media asset (not one per scraped URL variant).
- Download inputs are direct media/manifest URLs, not embed or player page URLs.
- Logs show raw candidate count vs collapsed logical count (sanitized output OK).
- At least one live download completes with a title-derived filename.
- Re-download of the same title uses a collision suffix; no silent overwrite.

## Fallback paths (spot-check)

- **Embedded-page video** — direct media URL found when present on page.
- **Direct HLS** — m3u8 detected; FFmpeg download succeeds.
- **Selenium fallback** — UI stays responsive while browser automation runs off the UI thread.

## Packaged build (Windows exe)

After `build_windows.bat`, the `release/Link2Vid/` folder opens automatically. Start via `Link2Vid.bat` (smoke also runs during build). Optional: `packaging\smoke_frozen.bat`. Then spot-check using the scenarios above. See [windows-packaging.md](./windows-packaging.md) for layout.

Additional frozen-only checks:

- Startup log shows `runtime: frozen=True ...` before the `UI runtime:` line.
- Copy Diagnostics reports ffmpeg under `\bin\` when `bin/ffmpeg.exe` is staged.
- `developer.json` beside `Link2Vid.exe` (or in `%APPDATA%\Link2Vid\`) prefills `default_url` when configured; bootstrap log shows resolved path.
- One HLS or Best (A+V) download succeeds with staged ffmpeg and a JS runtime (system or `bin/deno.exe`).
