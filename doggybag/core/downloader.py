"""Download/extraction logic extracted from the UI layer."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable

from .cookie_prefs import normalize_cookies_browser
from .errors import CookiesRequiredError, DownloadStalledError, NoTranscriptAvailableError
from .helpers import AUDIO_EXTRACT_FORMAT, audio_extract_format_selector, audio_extract_postprocessors
import sys
import subprocess
import re
import shutil
import time
from urllib.parse import urlparse
import yt_dlp

# YouTube limits unverified clients to roughly the first mebibyte of a format URL
# and answers every later range with 403. These clients have been observed to
# still serve whole files in that state; they are tried in order after a 403.
YOUTUBE_PLAYER_CLIENT_FALLBACKS = ("web_embedded", "tv_embedded", "mweb")

# The same throttling sometimes shows up as a crawl (tens of KiB/s) instead of a
# 403. A YouTube download that stays below this rate for a whole window, with a
# long way still to go, is treated like a media 403 and retried on other clients.
STALL_GRACE_SECONDS = 15
STALL_WINDOW_SECONDS = 30
STALL_MIN_BYTES_PER_SECOND = 64 * 1024
STALL_MIN_REMAINING_SECONDS = 120


class _StallWatch:
    """Progress hook that raises ``DownloadStalledError`` when a download crawls."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.samples: dict[str, deque] = {}
        self.started: dict[str, float] = {}

    def __call__(self, d: dict) -> None:
        if d.get("status") != "downloading":
            return
        downloaded = d.get("downloaded_bytes")
        if not isinstance(downloaded, (int, float)):
            return
        key = str(d.get("tmpfilename") or d.get("filename") or "")
        now = self.clock()
        started = self.started.setdefault(key, now)
        samples = self.samples.setdefault(key, deque())
        samples.append((now, float(downloaded)))
        while samples and now - samples[0][0] > STALL_WINDOW_SECONDS:
            samples.popleft()
        if now - started < STALL_GRACE_SECONDS + STALL_WINDOW_SECONDS:
            return
        span = now - samples[0][0]
        if span < STALL_WINDOW_SECONDS / 2:
            return
        rate = (samples[-1][1] - samples[0][1]) / span
        if rate >= STALL_MIN_BYTES_PER_SECOND:
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if isinstance(total, (int, float)) and total > 0 and rate > 0:
            if (total - downloaded) / rate < STALL_MIN_REMAINING_SECONDS:
                return
        raise DownloadStalledError(rate=rate, window=span)


@dataclass
class TranscriptDownloadResult:
    source: str
    languages: list[str]


@dataclass(frozen=True)
class TranscriptTrack:
    source: str
    language: str
    label: str


class DownloadManager:
    def __init__(
        self,
        *,
        ydl_logger,
        log: Callable[[str], None] | None = None,
        log_error: Callable[[str, Exception], None] | None = None,
        dev_defaults: dict | None = None,
        get_cookies_path: Callable[[], str | None] | None = None,
        get_cookies_browser: Callable[[], str | None] | None = None,
    ) -> None:
        self.ydl_logger = ydl_logger
        self.log = log or (lambda _msg: None)
        self.log_error = log_error or (lambda _stage, _err: None)
        self.dev_defaults = dev_defaults or {}
        self.get_cookies_path = get_cookies_path or (lambda: None)
        self.get_cookies_browser = get_cookies_browser or (
            lambda: normalize_cookies_browser((self.dev_defaults or {}).get("cookies_browser"))
        )
        self.last_cookies_mode = "none"
        self.last_cookies_browser = None
        # Set when the user picks a browser *in this session* (footer menu, or the
        # prompt shown after a site demanded sign-in). A merely persisted
        # preference must not force cookies onto every first attempt, but an
        # explicit choice has to be honoured immediately.
        self.force_browser_cookies = False
        self.last_player_client = None
        self.last_js_runtime = None
        self.last_js_runtime_path = None
        self.last_remote_components = []
        self._clock = time.monotonic

    def _preferred_browser(self) -> str | None:
        return normalize_cookies_browser(self.get_cookies_browser())

    def _running_browsers(self) -> list[str]:
        candidates: list[str] = []
        output = ""
        if sys.platform.startswith("win"):
            try:
                result = subprocess.run(["tasklist"], capture_output=True, text=True, check=False)
                output = result.stdout.lower()
            except Exception:
                return []
            process_map = {
                "msedge.exe": "edge",
                "brave.exe": "brave",
                "chrome.exe": "chrome",
                "firefox.exe": "firefox",
            }
        else:
            try:
                result = subprocess.run(["ps", "-A"], capture_output=True, text=True, check=False)
                output = result.stdout.lower()
            except Exception:
                return []
            process_map = {
                "microsoft-edge": "edge",
                "msedge": "edge",
                "brave-browser": "brave",
                "brave": "brave",
                "chrome": "chrome",
                "chromium": "chrome",
                "firefox": "firefox",
            }
        for process, browser in process_map.items():
            if process in output:
                candidates.append(browser)
        return candidates

    def _reset_cookie_state(self) -> None:
        self.last_cookies_mode = "none"
        self.last_cookies_browser = None

    def _apply_cookies(self, opts: dict) -> None:
        """Cookies for the *first* attempt.

        An explicit cookies.txt is a deliberate user choice, so it is honoured up
        front. Browser cookies are NOT: reading them is slow, fails whenever the
        browser is running (locked DB), and on YouTube an authenticated session
        without a matching PO token produces media URLs that 403 on public videos.
        They are escalated to only when the error says authentication is needed.
        """
        cookies_path = self.get_cookies_path()
        if cookies_path:
            opts["cookiefile"] = cookies_path
            self.last_cookies_mode = "cookies.txt"
            return
        browser = self._preferred_browser()
        if not browser:
            return
        if self.force_browser_cookies:
            opts["cookiesfrombrowser"] = (browser,)
            self._mark_browser_cookies(browser)
            self.log(f"[yt-dlp] Using cookies from {browser} (chosen for this session)")
            return
        self.log(
            f"[yt-dlp] Cookie source is {browser}; trying without cookies first "
            "(browser cookies are used only if the site asks for sign-in)."
        )

    def _mark_browser_cookies(self, browser: str) -> None:
        self.last_cookies_mode = "browser"
        self.last_cookies_browser = browser

    def _is_cookie_error(self, err: Exception) -> bool:
        if self._is_cookie_access_error(err):
            return True
        message = str(err).lower()
        # Only signals that genuinely mean "this content needs an authenticated
        # session". A bare 403/Forbidden is deliberately NOT here: YouTube returns
        # 403 on media stream URLs for throttling/PO-token reasons on public
        # videos, and escalating those to cookies makes the failure worse while
        # reporting a misleading "cookies/auth" cause.
        signals = (
            "account",
            "cookie",
            "cookies",
            "consent",
            "sign in",
            "signin",
            "log in",
            "login",
            "private video",
            "members-only",
            "members only",
            "age-restricted",
            "age restricted",
            "age verification",
            "confirm you're not a bot",
            "confirm your age",
            "bot",
            "captcha",
        )
        return any(token in message for token in signals)

    def _is_media_forbidden(self, err: Exception) -> bool:
        message = str(err).lower()
        return "403" in message or "forbidden" in message

    def _is_media_stall(self, err: Exception) -> bool:
        seen = set()
        current: BaseException | None = err
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, DownloadStalledError):
                return True
            current = current.__cause__ or current.__context__
        message = str(err).lower()
        return any(token in message for token in ("download stalled", "timed out", "timeout"))

    def _is_youtube(self, url: str) -> bool:
        host = (urlparse(url).netloc or "").lower()
        return host.endswith("youtube.com") or host.endswith("youtu.be") or ".youtube." in host

    def _with_player_client(self, opts: dict, client: str) -> dict:
        """Copy opts with youtube:player_client forced to a single client."""
        new_opts = dict(opts)
        extractor_args = {
            key: dict(value) for key, value in (opts.get("extractor_args") or {}).items()
        }
        youtube_args = dict(extractor_args.get("youtube") or {})
        youtube_args["player_client"] = [client]
        extractor_args["youtube"] = youtube_args
        new_opts["extractor_args"] = extractor_args
        return new_opts

    def _cookiesfrombrowser(self, opts: dict) -> str | None:
        value = opts.get("cookiesfrombrowser")
        if isinstance(value, tuple) and value:
            browser = value[0]
            return browser if isinstance(browser, str) and browser else None
        return None

    def _retry_with_browser_cookies(self, url: str, opts: dict, first_err: Exception, attempt):
        """Try remaining browsers, then a cookieless retry if the first attempt used cookies."""
        site_label = self._site_label(url)
        last_err = first_err
        already = self._cookiesfrombrowser(opts)
        if already:
            self.log(
                f"[yt-dlp] {already} cookies failed; trying other browsers for {site_label}."
            )
        candidates = [browser for browser in self._browser_candidates() if browser != already]
        if candidates:
            self.log(f"[yt-dlp] Browser cookie candidates: {', '.join(candidates)}")
        for browser in candidates:
            self.log(f"[yt-dlp] Retry with cookies from browser ({browser}) for {site_label}…")
            try:
                retry_opts = dict(opts)
                retry_opts.pop("cookiefile", None)
                retry_opts["cookiesfrombrowser"] = (browser,)
                result = attempt(retry_opts)
                self._mark_browser_cookies(browser)
                return result
            except Exception as retry_err:
                last_err = self._note_browser_cookie_failure(browser, retry_err, last_err)
                continue
        self.log("[yt-dlp] All browser cookie attempts failed.")
        if already:
            self.log("[yt-dlp] Retrying without browser cookies…")
            bare_opts = dict(opts)
            bare_opts.pop("cookiesfrombrowser", None)
            try:
                result = attempt(bare_opts)
                self.last_cookies_mode = "none"
                self.last_cookies_browser = None
                return result
            except Exception as bare_err:
                self.log(f"[yt-dlp] Cookieless retry failed: {self._format_exception(bare_err)}")
                if not self._is_cookie_access_error(bare_err):
                    last_err = bare_err
        raise CookiesRequiredError(original=last_err) from first_err

    def _browser_candidates(self) -> list[str]:
        ordered: list[str] = []
        seen = set()

        preferred = self._preferred_browser()
        if preferred and preferred not in seen:
            seen.add(preferred)
            ordered.append(preferred)

        for browser in self._running_browsers():
            if browser not in seen:
                seen.add(browser)
                ordered.append(browser)

        fallback = ["edge", "chrome", "brave", "firefox"]
        for browser in fallback:
            if browser not in seen:
                seen.add(browser)
                ordered.append(browser)

        return ordered

    def _select_js_runtime(self) -> tuple[str | None, str | None]:
        candidates = [
            ("deno", "deno"),
            ("node", "node"),
            ("bun", "bun"),
            ("quickjs", "qjs"),
        ]
        for runtime, exe in candidates:
            path = shutil.which(exe)
            if path:
                return runtime, path
        return None, None

    def _apply_js_runtime_opts(self, opts: dict) -> None:
        runtime, path = self._select_js_runtime()
        self.last_js_runtime = runtime
        self.last_js_runtime_path = path
        if runtime:
            opts["js_runtimes"] = {runtime: {"path": path} if path else {}}
            components = ["ejs:github"]
            if runtime in ("deno", "bun"):
                components.append("ejs:npm")
            opts["remote_components"] = components
            self.last_remote_components = components
            self.log(f"[yt-dlp] JS runtime selected: {runtime} ({path})")
            self.log(f"[yt-dlp] EJS remote components enabled: {', '.join(components)}")
            try:
                import yt_dlp_ejs
                version = getattr(yt_dlp_ejs, "__version__", "unknown")
                self.log(f"[yt-dlp] EJS package detected: yt-dlp-ejs {version}")
            except Exception:
                self.log("[yt-dlp] EJS package not detected; relying on remote components.")
        else:
            self.last_remote_components = []
            self.log("[yt-dlp] No JS runtime found; EJS challenge solver will be unavailable.")

    def _strip_ansi(self, text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", text or "")

    def _format_exception(self, err: Exception) -> str:
        text = self._strip_ansi(str(err) or "")
        if len(text) > 240:
            text = text[:237] + "..."
        if text:
            return f"{type(err).__name__}: {text}"
        return type(err).__name__

    def _cookie_failure_hint(self, err: Exception) -> str | None:
        msg = self._strip_ansi(str(err) or "").lower()
        if "dpapi" in msg:
            return "Hint: Windows could not decrypt Chrome cookies (DPAPI). Doggybag will try other browsers, then continue without cookies."
        if "could not copy chrome cookie database" in msg:
            return "Hint: Browser cookie database is locked. Fully close the browser (including background processes) and retry."
        if "could not find" in msg and "cookies database" in msg:
            if "firefox" in msg:
                return "Hint: Firefox profile not found on this machine."
            if "edge" in msg:
                return "Hint: Edge profile not found on this machine."
            if "chrome" in msg:
                return "Hint: Chrome profile not found on this machine."
            return "Hint: Browser cookie profile not found on this machine."
        return None

    def _is_cookie_access_error(self, err: Exception) -> bool:
        return self._cookie_failure_hint(err) is not None

    def _note_browser_cookie_failure(self, browser: str, retry_err: Exception, last_err: Exception) -> Exception:
        self.log(f"[yt-dlp] Browser cookies ({browser}) failed: {self._format_exception(retry_err)}")
        hint = self._cookie_failure_hint(retry_err)
        if hint:
            self.log(f"[yt-dlp] {hint}")
            return last_err
        return retry_err

    def _should_try_browser_cookies(self, url: str, err: Exception) -> bool:
        return self._is_cookie_error(err)

    def _site_label(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc or "this site"

    def _available_transcript_languages(self, tracks: dict | None) -> list[str]:
        if not isinstance(tracks, dict):
            return []
        languages: list[str] = []
        for language, entries in tracks.items():
            if language == "live_chat":
                continue
            if entries:
                languages.append(str(language))
        return sorted(languages)

    def _transcript_track_label(self, source: str, language: str, entries: list | tuple | None) -> str:
        name = ""
        if entries:
            first_entry = entries[0]
            if isinstance(first_entry, dict):
                name = str(first_entry.get("name") or "").strip()
        base = f"{name} ({language})" if name else language
        source_label = "Subtitles" if source == "subtitles" else "Auto"
        return f"{base} - {source_label}"

    def _transcript_tracks_for_source(
        self,
        source: str,
        tracks: dict | None,
    ) -> list[TranscriptTrack]:
        if not isinstance(tracks, dict):
            return []
        result: list[TranscriptTrack] = []
        for language in self._available_transcript_languages(tracks):
            entries = tracks.get(language)
            result.append(
                TranscriptTrack(
                    source=source,
                    language=language,
                    label=self._transcript_track_label(source, language, entries),
                )
            )
        return sorted(result, key=lambda track: track.label.lower())

    def transcript_tracks_from_info(self, info: dict | None) -> list[TranscriptTrack]:
        info = info or {}
        return [
            *self._transcript_tracks_for_source("subtitles", info.get("subtitles")),
            *self._transcript_tracks_for_source("automatic captions", info.get("automatic_captions")),
        ]

    def _pick_preferred_transcript_language(self, languages: list[str], info: dict | None = None) -> str:
        if not languages:
            raise NoTranscriptAvailableError("No transcript/subtitles available for this video.")

        lowered = {language.lower(): language for language in languages}
        if "en" in lowered:
            return lowered["en"]

        english_variants = [language for language in languages if language.lower().startswith("en-")]
        if english_variants:
            return sorted(english_variants)[0]

        info = info or {}
        original_language = str(info.get("language") or info.get("original_language") or "").strip().lower()
        if original_language:
            if original_language in lowered:
                return lowered[original_language]
            original_variants = [language for language in languages if language.lower().startswith(f"{original_language}-")]
            if original_variants:
                return sorted(original_variants)[0]

        original_track_variants = [language for language in languages if "orig" in language.lower()]
        if original_track_variants:
            return sorted(original_track_variants)[0]

        return languages[0]

    def _config_for_selected_transcript_track(self, info: dict, selected_track: TranscriptTrack) -> dict[str, object]:
        if selected_track.source == "subtitles":
            subtitle_langs = self._available_transcript_languages(info.get("subtitles"))
            if selected_track.language in subtitle_langs:
                return {
                    "source": "subtitles",
                    "languages": [selected_track.language],
                    "available_languages": subtitle_langs,
                    "writesubtitles": True,
                    "writeautomaticsub": False,
                }
        elif selected_track.source == "automatic captions":
            automatic_langs = self._available_transcript_languages(info.get("automatic_captions"))
            if selected_track.language in automatic_langs:
                return {
                    "source": "automatic captions",
                    "languages": [selected_track.language],
                    "available_languages": automatic_langs,
                    "writesubtitles": False,
                    "writeautomaticsub": True,
                }
        raise NoTranscriptAvailableError(
            f"Selected transcript language is unavailable: {selected_track.label}"
        )

    def _transcript_download_config(
        self,
        info: dict,
        selected_track: TranscriptTrack | None = None,
    ) -> dict[str, object]:
        if selected_track is not None:
            return self._config_for_selected_transcript_track(info, selected_track)

        subtitle_langs = self._available_transcript_languages(info.get("subtitles"))
        if subtitle_langs:
            selected = self._pick_preferred_transcript_language(subtitle_langs, info)
            return {
                "source": "subtitles",
                "languages": [selected],
                "available_languages": subtitle_langs,
                "writesubtitles": True,
                "writeautomaticsub": False,
            }

        automatic_langs = self._available_transcript_languages(info.get("automatic_captions"))
        if automatic_langs:
            selected = self._pick_preferred_transcript_language(automatic_langs, info)
            return {
                "source": "automatic captions",
                "languages": [selected],
                "available_languages": automatic_langs,
                "writesubtitles": False,
                "writeautomaticsub": True,
            }

        raise NoTranscriptAvailableError("No transcript/subtitles available for this video.")

    def get_video_info(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        *,
        flat: bool = False,
    ):
        self._reset_cookie_state()
        ydl_opts = {"quiet": True, "skip_download": True, "logger": self.ydl_logger}
        if flat:
            ydl_opts["extract_flat"] = "in_playlist"
        if username and password:
            ydl_opts["username"] = username
            ydl_opts["password"] = password
        self._apply_cookies(ydl_opts)
        self._apply_js_runtime_opts(ydl_opts)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if "entries" in info:
                    return info["entries"]
                return [info]
        except Exception as first_err:
            if not self._should_try_browser_cookies(url, first_err):
                raise

            def extract(retry_opts: dict):
                with yt_dlp.YoutubeDL(retry_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                if "entries" in info:
                    return info["entries"]
                return [info]

            return self._retry_with_browser_cookies(url, ydl_opts, first_err, extract)

    def download(
        self,
        url: str,
        format_id: str,
        out_path: str,
        progress_hook: Callable | None = None,
        post_hook: Callable[[str], None] | None = None,
        extract_audio: bool = False,
        *,
        raise_errors: bool = False,
    ) -> bool:
        """Download ``url`` with yt-dlp; ``out_path`` is an outtmpl.

        By default a final non-cookie failure goes to ``log_error`` and returns
        False. ``raise_errors=True`` raises it instead so callers can classify it.
        """
        self._reset_cookie_state()
        self.last_player_client = None
        resolved_format = format_id
        if extract_audio and format_id == AUDIO_EXTRACT_FORMAT:
            resolved_format = audio_extract_format_selector()
            self.log(f"[yt-dlp] Audio extract format: {resolved_format}")
        opts = {
            "format": resolved_format,
            "outtmpl": out_path,
            "progress_hooks": [progress_hook] if progress_hook else [],
            "post_hooks": [post_hook] if post_hook else [],
            "quiet": True,
            "logger": self.ydl_logger,
        }
        if extract_audio:
            opts["postprocessors"] = audio_extract_postprocessors()
        self._apply_cookies(opts)
        self._apply_js_runtime_opts(opts)

        guard_stalls = self._is_youtube(url)

        def download_with(retry_opts: dict, *, watch_stalls: bool = True):
            if guard_stalls and watch_stalls:
                retry_opts = dict(retry_opts)
                retry_opts["progress_hooks"] = [
                    *(retry_opts.get("progress_hooks") or []),
                    _StallWatch(clock=self._clock),
                ]
            with yt_dlp.YoutubeDL(retry_opts) as ydl:
                ydl.download([url])
            return True

        def retry_audio_from_muxed(err: Exception) -> bool:
            if not extract_audio or (opts.get("format") or "") == "best":
                return False
            original = getattr(err, "original", None)
            if not (self._is_media_forbidden(err) or (original is not None and self._is_media_forbidden(original))):
                return False
            self.log("[yt-dlp] Audio-only stream blocked; extracting MP3 from a muxed file instead.")
            muxed_opts = dict(opts)
            muxed_opts["format"] = "best"
            try:
                return download_with(muxed_opts)
            except Exception as muxed_err:
                self.log(f"[yt-dlp] Muxed audio extract failed: {self._format_exception(muxed_err)}")
                return False

        def retry_player_clients(err: Exception) -> bool:
            """Retry a media-stream 403 or stall against alternative YouTube player clients.

            YouTube serves only the first ~1 MiB of a format URL to clients it
            considers unverified, then answers every further range with 403 (or
            throttles it to a crawl). The default client can hit this while an
            embedded client still serves the whole file, so walk the alternatives
            before giving up. Resume is disabled because a stale .part would
            range-request a newly signed URL.
            """
            if not self._is_youtube(url):
                return False
            stalled = self._is_media_stall(err)
            if not (stalled or self._is_media_forbidden(err)):
                return False
            reason = "Download stalled" if stalled else "Media 403"
            any_stalled = stalled
            for client in YOUTUBE_PLAYER_CLIENT_FALLBACKS:
                self.log(f"[yt-dlp] {reason}; retrying with YouTube player client '{client}'…")
                retry_opts = self._with_player_client(opts, client)
                retry_opts["continuedl"] = False
                try:
                    if download_with(retry_opts):
                        self.last_player_client = client
                        self.log(f"[yt-dlp] Player client '{client}' succeeded.")
                        return True
                except Exception as retry_err:
                    any_stalled = any_stalled or self._is_media_stall(retry_err)
                    self.log(
                        f"[yt-dlp] Player client '{client}' failed: {self._format_exception(retry_err)}"
                    )
            if any_stalled:
                # Every client crawled: the connection may just be slow. Finish
                # the download unguarded rather than fail where it used to succeed.
                self.log("[yt-dlp] All player clients were slow; retrying slow but steady without the stall guard…")
                retry_opts = dict(opts)
                retry_opts["continuedl"] = False
                return download_with(retry_opts, watch_stalls=False)
            return False

        try:
            return download_with(opts)
        except Exception as first_err:
            try:
                if retry_player_clients(first_err):
                    return True
            except Exception as unguarded_err:
                if raise_errors:
                    raise
                self.log_error("Download", unguarded_err)
                return False
            if self._should_try_browser_cookies(url, first_err):
                try:
                    return self._retry_with_browser_cookies(url, opts, first_err, download_with)
                except CookiesRequiredError as cookie_err:
                    if retry_audio_from_muxed(cookie_err):
                        return True
                    raise
            if retry_audio_from_muxed(first_err):
                return True
            if raise_errors:
                raise first_err
            self.log_error("Download", first_err)
            return False

    def download_transcript(
        self,
        url: str,
        out_path: str,
        selected_track: TranscriptTrack | None = None,
    ) -> TranscriptDownloadResult:
        self._reset_cookie_state()
        opts = {
            "skip_download": True,
            "outtmpl": out_path,
            "quiet": True,
            "logger": self.ydl_logger,
        }
        self._apply_cookies(opts)
        self._apply_js_runtime_opts(opts)

        def attempt(current_opts: dict) -> TranscriptDownloadResult:
            with yt_dlp.YoutubeDL(current_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                config = self._transcript_download_config(info or {}, selected_track=selected_track)

            selected_languages = list(config["languages"])
            available_languages = list(config.get("available_languages", selected_languages))
            if available_languages:
                selected_label = ", ".join(selected_languages)
                available_label = ", ".join(available_languages)
                self.log(
                    f"[yt-dlp] Transcript selection: {config['source']} -> {selected_label} "
                    f"(available: {available_label})"
                )

            download_opts = dict(current_opts)
            download_opts.update(
                {
                    "writesubtitles": bool(config["writesubtitles"]),
                    "writeautomaticsub": bool(config["writeautomaticsub"]),
                    "subtitleslangs": list(config["languages"]),
                }
            )
            with yt_dlp.YoutubeDL(download_opts) as ydl:
                ydl.download([url])
            return TranscriptDownloadResult(
                source=str(config["source"]),
                languages=list(config["languages"]),
            )

        try:
            return attempt(opts)
        except Exception as first_err:
            if not self._should_try_browser_cookies(url, first_err):
                raise
            return self._retry_with_browser_cookies(url, opts, first_err, attempt)
