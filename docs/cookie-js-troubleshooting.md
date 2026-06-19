# Cookie and JavaScript troubleshooting

Practical fixes for common extraction and download failures in Link2Vid.

For app architecture and fetch routing, see [architecture.md](./architecture.md).

## Quick checks

1. **Update yt-dlp**
   ```bash
   pip install -U yt-dlp
   ```
   Or re-run `setup_link2vid.bat` to refresh dependencies in your venv.

2. **Install ffmpeg**
   - Required for merging best video + audio.
   - Ensure `ffmpeg` is on your PATH.

3. **Install a JavaScript runtime**
   - yt-dlp needs a JS runtime for some protected or dynamically generated formats.
   - Install **deno**, **node**, or **bun** and ensure it is on PATH.
   - Link2Vid lets yt-dlp fetch EJS solver scripts from GitHub automatically.
   - If GitHub is blocked, install bundled scripts: `pip install -U "yt-dlp[default]"`.

4. **Use cookies for restricted videos**
   - For bot/age/consent errors, Link2Vid tries browser cookies first (Edge/Chrome/Brave/Firefox).
   - Set preferred browser in `developer.json` → `cookies_browser`.
   - If that fails, export cookies and select `cookies.txt` in the app.

## Common errors

| Symptom | Likely cause | Fix |
|---|---|---|
| Signature / cipher / extractor errors | Outdated yt-dlp | Update yt-dlp |
| 403 / Forbidden / bot check / sign in | Auth or cookies required | Browser cookies or `cookies.txt` |
| No supported JavaScript runtime | Missing deno/node/bun | Install runtime, retry |
| n challenge / only images available | EJS scripts missing or blocked | JS runtime + `yt-dlp[default]` |
| No audio / silent video | Video-only format selected | Use **Best (A+V)** |
| ffmpeg missing | System tool not installed | Install ffmpeg |

## Fallback paths

If yt-dlp fails, Link2Vid may try an embedded-page scrape, direct HTTP media scan, HLS detection, or Selenium with your confirmation. These paths are fallback routes for pages where a direct media URL can be discovered.

## Reporting issues

Use **Copy Diagnostics** in the app and include:

- URL (sanitized if needed)
- Selected format
- yt-dlp version, ffmpeg path, JS runtime
- Cookies mode
- Last error and recent logs

See [verification-checklist.md](./verification-checklist.md) for full regression scenarios.
