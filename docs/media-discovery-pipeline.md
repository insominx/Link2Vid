# Media discovery pipeline

How Link2Vid discovers multiple videos and chooses downloadable URLs. For overall fetch routing, see [architecture.md](./architecture.md). For generic integration patterns, see [agent-playbook-external-integrations.md](./agent-playbook-external-integrations.md).

## Fetch routing

Full chain is in [architecture.md](./architecture.md). Selenium-specific behavior:

1. `VideoFetcher.fetch` tries yt-dlp, configured embedded-page scrape, HTTP direct scan, and HLS scan before returning `NeedsSelenium`.
2. **Unauthenticated HTTP path:** `scan_direct_media_entries` in `link2vid/core/extractors.py` returns direct media URLs from page HTML as card entries via `build_media_entries`.
3. **Selenium path:** `selenium_fetch_media_entries` in `link2vid/core/selenium_fallback.py` logs in when needed, runs `discover_media_urls`, attaches titles/headers, returns entries for the card list.

User selects a card; entries with `_ffmpeg_headers` download via ffmpeg (`_download_direct_media`), others via yt-dlp.

## Selenium candidate normalization (authority)

**Owner:** `link2vid/core/selenium_fallback.py`

**Decision point:** `discover_media_urls` collects raw candidates (HTML, DOM, network logs, iframes), dedupes with broad `is_media_url`, then passes the list through `collapse_selenium_media_candidates` before return.

**Invariants:**

- Embed/player page URLs are collected but **never returned** as downloadable outputs.
- Duplicate CDN variants for one logical video collapse to one URL, grouped by stable vimeocdn path UUID when present.
- Output order follows first-seen group appearance in the raw candidate stream.
- Player-only candidate sets yield an empty playable list so follow-up-page / no-media behavior runs.

**Test seam:** `collapse_selenium_media_candidates` — pure function, table-driven tests in `tests/test_selenium_fallback.py`.

## Filenames and output paths

- Card titles come from page title, URL slug, DOM labels (best-effort), or numbered suffix (`Page Title - 1`).
- Downloads use `sanitize_filename` + `unique_output_path` in `link2vid/core/helpers.py` (collision suffix, no overwrite).

## Verification

```text
python -m pytest tests/test_selenium_fallback.py
python -m pytest tests/test_ui_progress.py
python -m pytest tests/
```

For authenticated multi-item pages, also confirm: raw candidate count vs collapsed logical count in logs; no embed URLs in card `webpage_url`; at least one successful ffmpeg download with a title-derived filename. See `verification-checklist.md` § Authenticated / multi-item external pages.

## Known gaps

- HTTP scan path does not apply the same Vimeo UUID collapse as Selenium.
- Per-video DOM titles may not align with collapsed URL order on all page shapes.
- Signed HLS URLs may expire between fetch and download.
