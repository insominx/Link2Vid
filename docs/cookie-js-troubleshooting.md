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
   - Choose a browser in the footer **Cookies** menu. Link2Vid reads that browser's cookies automatically.
   - For bot/age/consent errors it also tries other installed browsers.
   - If the chosen browser is open, fully quit it (including background/tray processes) and retry.
   - Fetch can succeed on a public YouTube URL while the later media download still gets HTTP 403. For **Audio (MP3)**, that is often YouTube blocking audio-only DASH URLs, not missing cookies — Link2Vid then extracts MP3 from the same muxed file **Best (A+V)** uses.

## Windows browser cookies

yt-dlp has to *copy and decrypt* the browser cookie database. A listed browser is not enough.

| Symptom | Meaning | What to do |
|---|---|---|
| Could not copy Chrome cookie database | Brave/Chrome/Edge is still running and the DB is locked | Fully quit the browser, including background/tray processes, then retry |
| Failed to decrypt with DPAPI | Chrome App-Bound Encryption blocked cookie decryption | Link2Vid continues with other browsers, then without cookies. Prefer Brave/Firefox in the footer, and fully quit that browser first |
| Could not find firefox/edge cookies database | That browser is not installed, or has no profile | Ignore it; choose the browser you actually use in the footer |

On a machine that only has Brave (or only Chrome), Edge/Firefox attempts will always fail. Close the browser you are logged into, set it in the footer **Cookies** menu, and retry.

## Common errors

| Symptom | Likely cause | Fix |
|---|---|---|
| Signature / cipher / extractor errors | Outdated yt-dlp | Update yt-dlp |
| 403 / Forbidden / bot check / sign in | Auth, cookies, or YouTube blocking audio-only URLs | For video, choose a browser in the footer and close it. If **Best (A+V)** works but **Audio (MP3)** 403s, retry Audio — Link2Vid extracts MP3 from the muxed file |
| No supported JavaScript runtime | Missing deno/node/bun | Install runtime, retry |
| n challenge / only images available | EJS scripts missing or blocked | JS runtime + `yt-dlp[default]` |
| No audio / silent video | Video-only format selected | Use **Best (A+V)** |
| Audio saved as `.webm` / `.m4a` instead of MP3 | ffmpeg missing | Install ffmpeg, then choose **Audio (MP3)** again |
| ffmpeg missing | System tool not installed | Install ffmpeg |

## Fallback paths

If yt-dlp fails, Link2Vid may try an embedded-page scrape, direct HTTP media scan, HLS detection, or Selenium with your confirmation. These paths are fallback routes for pages where a direct media URL can be discovered.

Login-gated community pages that redirect yt-dlp to a site login URL (or that match credentials in local `developer.json`) are routed to the Selenium confirmation path. Ordinary cookie challenges on unconfigured public sites use the footer browser picker and automatic browser-cookie lookup.

## Reporting issues

Use **Copy Diagnostics** in the app and include:

- URL (sanitized if needed)
- Selected format
- yt-dlp version, ffmpeg path, JS runtime
- Cookies mode
- Last error and recent logs

See [verification-checklist.md](./verification-checklist.md) for full regression scenarios.
