# Link2Vid

Link2Vid is a desktop application for downloading videos from various platforms using a simple and modern graphical interface. It leverages [yt-dlp](https://github.com/yt-dlp/yt-dlp) for video extraction and download, and is built with [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) for a sleek, dark-themed UI.

## Features
- Download videos from a wide range of sites supported by yt-dlp
- Modern, dark-themed GUI
- Select and preview available video formats
- Choose download directory
- Download transcript/caption files without downloading video/audio media
- Create local speech-to-text transcripts with Faster Whisper (`.txt` and `.srt`)
- Progress bar and log output

## Requirements
- Python 3.10+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)

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
   Or directly: `pythonw video_downloader.py`
3. Paste a video or playlist URL into the input field.
4. Click **Fetch** to load result cards and available formats.
5. Scroll the card list; use **Load more** for large playlists.
6. Choose a download folder in the footer.
7. Click **Download** to save media, **Transcript** to save provider captions, or choose a model and click **Whisper** to transcribe speech locally.

## Local Whisper transcription

Whisper is independent of provider captions. It reuses the exact media saved by a successful download when available; otherwise it downloads temporary best-quality audio and removes that temporary workspace after the run. Each successful action writes collision-safe `<title>.whisper.<language>.txt` and `<title>.whisper.<language>.srt` files to the selected output folder.

Model weights are downloaded on first use and cached outside the app at `%LOCALAPPDATA%\Link2Vid\models` (or `~/.link2vid/models` when `LOCALAPPDATA` is unavailable). They are not included in portable builds, so first use needs network access and sufficient disk space. Link2Vid uses CUDA when CTranslate2 reports an available GPU and otherwise uses CPU. CUDA model-loading errors are reported rather than silently retried; acceleration requires a working driver/runtime compatible with CTranslate2.

## Documentation

- [docs/INDEX.md](docs/INDEX.md) — doc map
- [docs/architecture.md](docs/architecture.md) — how the app is structured
- [docs/verification-checklist.md](docs/verification-checklist.md) — manual regression checklist
- [docs/windows-packaging.md](docs/windows-packaging.md) — build a Windows `.exe` (PyInstaller onedir)

## Building a Windows executable

On Windows, run `build_windows.bat` to produce a portable app in `release/Link2Vid/`. When the build finishes, Explorer opens that folder — **double-click `Link2Vid.bat`** to start. You can also use `run_link2vid_packaged.bat` from the repo root. See [docs/windows-packaging.md](docs/windows-packaging.md) for prerequisites, sidecar binaries, and `developer.json` handling.

## License
This project is licensed under the MIT License. See [LICENSE.md](LICENSE.md) for details.
