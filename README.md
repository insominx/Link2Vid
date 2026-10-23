# Link2Vid

Link2Vid is a desktop app for saving video, audio, captions, and local speech-to-text transcripts from a link. It uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) for extraction and download and [Faster Whisper](https://github.com/SYSTRAN/faster-whisper) for transcription. The engine is Python; the interface is a native window ([pywebview](https://pywebview.flowrl.com/) + WebView2) with a hand-built HTML/CSS frontend that follows the Windows light/dark setting.

## Features
- Download videos from the wide range of sites yt-dlp supports, plus playlists and channels
- Fetch public [Udio](https://www.udio.com) `/songs/` links without login (Udio email-link sign-in is not automated)
- Choose **Video** (best quality with sound), **Video only**, or **Audio** (MP3) per item
- Save the site's captions in any listed language without downloading media
- Create local speech-to-text transcripts with Faster Whisper (`.txt` and `.srt`)
- Select playlist/channel entries and batch-download media or captions
- Open or reveal finished files straight from each card
- Paste anywhere (`Ctrl+V`) or drop a link on the window to fetch it
- Live Activity log and one-click **Copy diagnostics** for bug reports

## Requirements
- Python 3.10+
- Microsoft Edge WebView2 Runtime (preinstalled on Windows 11 and current Windows 10)
- Python packages from `requirements.txt` (yt-dlp, pywebview, faster-whisper, selenium, ...)
- Optional: ffmpeg (needed to merge video+audio and to make MP3s) and a JS runtime (deno/node/bun) for challenge-protected YouTube formats

Install dependencies with:
```bash
pip install -r requirements.txt
```

## Usage
1. Run setup once:
   ```bash
   setup_link2vid.bat
   ```
   Optional (recommended): create a local venv first with `python -m venv .venv`.
2. Run the application:
   ```bash
   run_link2vid.bat
   ```
   Or directly: `pythonw video_downloader.py` (add `--devtools` to open the WebView developer tools).
3. Paste a video, playlist, or channel link and press **Fetch** (or just press `Ctrl+V` anywhere in the window).
4. For a single video, use **Download** (the chevron next to it picks Video, Video only, or Audio) or **Transcript** to save captions or run Whisper with a chosen model.
5. For playlists, every item starts selected. Use each card's checkbox or the header checkbox to change the selection; it covers the whole list, including items that load as you scroll. **Download N** saves each selected item with its own format, and **Save captions** saves each item's provider captions. Batch runs are sequential; **Stop** finishes the current item and skips the rest.
6. The status bar at the bottom shows the download folder (click to change it, or use the folder icon to open it) and the **Cookies** source. If a site asks you to sign in, pick the browser you use for it (close that browser first), or point Link2Vid at a `cookies.txt` export.
7. Finished cards offer **Open** and **Show in folder**; failed cards offer **Retry** and **Details**, which opens the Activity log (`Ctrl+J`).

Batch actions produce the same individual files as the corresponding per-card actions; they never create a combined archive or media file. Batch captions use provider captions and do not run Whisper.

## Local Whisper transcription

Whisper is independent of provider captions. It reuses the exact media saved by a successful download when available; otherwise it downloads temporary best-quality audio and removes that temporary workspace after the run. Each successful action writes collision-safe `<title>.whisper.<language>.txt` and `<title>.whisper.<language>.srt` files to the selected output folder.

Model weights are downloaded on first use and cached outside the app at `%LOCALAPPDATA%\Link2Vid\models` (or `~/.link2vid/models` when `LOCALAPPDATA` is unavailable). They are not included in portable builds, so first use needs network access and sufficient disk space. Link2Vid uses CUDA when CTranslate2 reports an available GPU and otherwise uses CPU. CUDA model-loading errors are reported rather than silently retried; acceleration requires a working driver/runtime compatible with CTranslate2.

When available, **large-v3** is selected by default for maximum general-purpose transcription quality. It requires a larger model download and more compute than distilled or smaller models; choose `distil-large-v3.5` when English-only speed is more important.

## Documentation

- [docs/INDEX.md](docs/INDEX.md) — doc map
- [docs/architecture.md](docs/architecture.md) — how the app is structured
- [docs/ui-frontend.md](docs/ui-frontend.md) — the web frontend and how to iterate on it
- [docs/verification-checklist.md](docs/verification-checklist.md) — manual regression checklist
- [docs/windows-packaging.md](docs/windows-packaging.md) — build a Windows `.exe` (PyInstaller onedir)

## Building a Windows executable

On Windows, run `build_windows.bat` to produce a portable app in `release/Link2Vid/`. When the build finishes, Explorer opens that folder — **double-click `Link2Vid.bat`** to start. You can also use `run_link2vid_packaged.bat` from the repo root. See [docs/windows-packaging.md](docs/windows-packaging.md) for prerequisites, sidecar binaries, and `developer.json` handling.

## License
This project is licensed under the MIT License. See [LICENSE.md](LICENSE.md) for details.
