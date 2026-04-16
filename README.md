# Link2Vid

Link2Vid is a desktop application for downloading videos from various platforms using a simple and modern graphical interface. It leverages [yt-dlp](https://github.com/yt-dlp/yt-dlp) for video extraction and download, and is built with [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) for a sleek, dark-themed UI.

## Features
- Download videos from a wide range of sites supported by yt-dlp
- Modern, dark-themed GUI
- Select and preview available video formats
- Choose download directory
- Download transcript/caption files without downloading video/audio media
- Progress bar and log output

## Requirements
- Python 3.8+
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
   @echo off
   pythonw video_downloader.py
   ```
3. Paste a video or playlist URL into the input field.
4. Click **Fetch Videos** to load available videos and formats.
5. Select a video from the list.
6. Choose a download folder.
7. Click **Download** on a result card to save media, or **Transcript** to save transcript/caption files only.

### Notes for cookie-protected sites (X/Twitter, YouTube)
- Some videos require authentication or consent. The app supports two cookie methods for both media and transcript retrieval:
  - Browser cookies (automatic): The app will try Edge/Chrome/Brave/Firefox in order. You can set `cookies_browser` in `developer.json` to prefer one (`edge`, `chrome`, `brave`, or `firefox`).
  - cookies.txt (manual): Click "Select cookies.txt" and choose an exported cookie file for the site.
- If browser cookies cannot be decrypted (DPAPI error), the app will prompt you to provide a `cookies.txt`.
- How to export cookies.txt: Use an extension like "Get cookies.txt" in your browser to export cookies for the site.

### Notes for cookie-protected sites (X/Twitter, YouTube)
- The app retries with browser cookies for YouTube and X/Twitter cookie-gated errors.
- On Windows the default browser used is Edge. To change this, set `cookies_browser` in `developer.json` to one of: `edge`, `chrome`, or `firefox`.
- Example `developer.json` snippet:
  ```json
  {
    "use_defaults": true,
    "cookies_browser": "edge"
  }
  ```

## License
This project is licensed under the MIT License. See [LICENSE.md](LICENSE.md) for details.
