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
   - Choose a browser in the **Cookies** menu in the status bar. Link2Vid reads that browser's cookies automatically.
   - For bot/age/consent errors it also tries other installed browsers.
   - If the chosen browser is open, fully quit it (including background/tray processes) and retry.
   - Fetch can succeed on a public YouTube URL while the later media download still gets HTTP 403. For **Audio (MP3)**, that is often YouTube blocking audio-only DASH URLs, not missing cookies — Link2Vid then extracts MP3 from the same muxed file **Best (A+V)** uses.

## Windows browser cookies

yt-dlp has to *copy and decrypt* the browser cookie database, and on current Windows both steps can fail. Since Chrome 127, Chromium browsers encrypt cookies with **App-Bound Encryption**, which yt-dlp cannot decrypt at all ([yt-dlp #10927](https://github.com/yt-dlp/yt-dlp/issues/10927)) — closing the browser does not help. In practice this means **Chrome, Brave, and Edge cookies are unavailable**, whatever the Cookies menu offers.

| Symptom | Meaning | What to do |
|---|---|---|
| Failed to decrypt with DPAPI | App-Bound Encryption; the browser being closed makes no difference | Use Firefox, or a cookies.txt export. This browser can never supply cookies |
| Could not copy Chrome cookie database | That Chromium browser is running and the DB is locked | Fully quit it (including background/tray processes) — but note decryption may still fail for the reason above |
| Could not find firefox/edge cookies database | That browser is not installed, or has no profile | Ignore it; it is only a fallback candidate |

Because of this, Link2Vid tries **without cookies first** and only escalates to browser cookies when the site actually asks for sign-in. Choosing a browser in the Cookies menu marks it as a deliberate choice and uses it immediately for the rest of that session.

### cookies.txt export

For genuinely gated content (private, members-only, age-restricted), a cookies.txt export is the only cookie route that still works on Windows, since Chromium cookies cannot be decrypted at all (see above).

1. Install a cookies-export extension (e.g. "Get cookies.txt LOCALLY" for Chrome/Edge/Brave, or "cookies.txt" for Firefox).
2. Visit the site while logged in and export cookies to a `.txt` file (Netscape format).
3. In Link2Vid's status bar, open **Cookies** → **Use a cookies.txt file…** and select that file.
4. Link2Vid remembers the path and uses it for every download from then on.

Re-export the file when the session expires or after you sign out/in.

Do **not** reach for this to fix a plain 403 on a public video — that is the PO Token problem below, and cookies cannot fix it.

## HTTP 403 on public videos (the 1 MiB cap)

If public YouTube videos fail with `unable to download video data: HTTP Error 403: Forbidden`, you are not missing cookies. YouTube serves clients it considers unverified only the **first mebibyte** of a format URL and refuses everything after it. Confirmed against the media URL directly:

| Request | Result |
|---|---|
| `Range: bytes=0-1048576` | 206, full mebibyte returned |
| `Range: bytes=0-1500000` | 403 |
| `Range: bytes=1048576-2097151` | 403 |
| no `Range` header | 403 |

So any range starting past the first mebibyte, or larger than it, is refused. The signature is easy to recognise: metadata and format listing succeed, `--test` (first 10 KB) succeeds, and the full download 403s — on *every* video, not just one.

**The fix is a different player client.** The default client hits the cap while an embedded client still serves whole files:

```bash
yt-dlp --extractor-args "youtube:player_client=web_embedded" URL
```

Link2Vid does this automatically: on a media 403 it retries YouTube downloads against `web_embedded`, then `tv_embedded`, then `mweb`, and logs which one worked. Nothing to install and no cookies involved.

Things that do **not** fix this, all verified: updating yt-dlp, browser cookies, a cookies.txt export, `--http-chunk-size`, and running a PO Token provider (the token is minted for `web_safari`, which returns no formats, while media comes from `android_vr`, which yt-dlp never tokenises). If the fallback clients are ever capped too, the fix is to find a client that still works and add it to `YOUTUBE_PLAYER_CLIENT_FALLBACKS` in `link2vid/core/downloader.py` — not to install anything.

## Common errors

| Symptom | Likely cause | Fix |
|---|---|---|
| Signature / cipher / extractor errors | Outdated yt-dlp | Update yt-dlp |
| 403 / Forbidden / bot check / sign in | Auth, cookies, or YouTube blocking audio-only URLs | For video, choose a browser under **Cookies** and close it. If **Best (A+V)** works but **Audio (MP3)** 403s, retry Audio — Link2Vid extracts MP3 from the muxed file |
| No supported JavaScript runtime | Missing deno/node/bun | Install runtime, retry |
| n challenge / only images available | EJS scripts missing or blocked | JS runtime + `yt-dlp[default]` |
| No audio / silent video | Video-only format selected | Use **Best (A+V)** |
| Audio saved as `.webm` / `.m4a` instead of MP3 | ffmpeg missing | Install ffmpeg, then choose **Audio (MP3)** again |
| ffmpeg missing | System tool not installed | Install ffmpeg |

## Fallback paths

If yt-dlp fails, Link2Vid may try an embedded-page scrape, direct HTTP media scan, HLS detection, or Selenium with your confirmation. These paths are fallback routes for pages where a direct media URL can be discovered.

Login-gated community pages that redirect yt-dlp to a site login URL (or that match credentials in local `developer.json`) are routed to the Selenium confirmation path. Ordinary cookie challenges on unconfigured public sites use the Cookies browser picker and automatic browser-cookie lookup.

## Reporting issues

Use **Copy Diagnostics** in the app and include:

- URL (sanitized if needed)
- Selected format
- yt-dlp version, ffmpeg path, JS runtime
- Cookies mode
- Last error and recent logs

See [verification-checklist.md](./verification-checklist.md) for full regression scenarios.
