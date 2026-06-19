"""Selenium-based fallback helpers."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .dev_defaults import LoginPlan
from .extractors import (
    _clean_page_title,
    build_media_entries,
    extract_page_title,
    guess_video_titles,
    title_from_page_url,
)

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class SeleniumMediaResult:
    media_url: str
    headers: dict[str, str]

M3U8_RE = re.compile(r"https?://[^\"'\s]+\.m3u8[^\"'\s]*", re.I)
MP4_RE = re.compile(r"https?://[^\"'\s]+\.mp4[^\"'\s]*", re.I)
MUX_RE = re.compile(r"https?://stream\.mux\.com/[^\"'\s]+", re.I)
UUID_PATH_SEGMENT_RE = re.compile(
    r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)",
    re.I,
)
OG_VIDEO_RE = re.compile(
    r"<meta[^>]+property=[\"']og:video(?::url)?[\"'][^>]+content=[\"']([^\"']+)[\"']",
    re.I,
)
HTML_MEDIA_RES = (M3U8_RE, MP4_RE, MUX_RE)

EMAIL_SELECTORS = (
    "input[type='email']",
    "input[name='user[email]']",
    "input[name='email']",
    "input[autocomplete='email']",
)
PASSWORD_SELECTORS = (
    "input[type='password']",
    "input[name='user[password]']",
    "input[name='password']",
    "input[autocomplete='current-password']",
)
MEDIA_URL_HINTS = (
    ".m3u8",
    ".mp4",
    ".webm",
    "/manifest",
    "stream.mux.com",
    "spotlightr",
    "wistia",
    "cloudfront.net",
    "akamaized.net",
    "fastly.net",
    "googlevideo.com",
    "playlist",
)
MEDIA_URL_EXCLUDES = (
    "google-analytics",
    "googletagmanager",
    "segment.io",
    "hotjar",
    ".js",
    ".css",
    ".woff",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".gif",
    "favicon",
)


def is_media_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    if not lowered.startswith("http"):
        return False
    if any(token in lowered for token in MEDIA_URL_EXCLUDES):
        return False
    return any(hint in lowered for hint in MEDIA_URL_HINTS)


def collect_media_urls_from_html(html: str) -> list[str]:
    urls: list[str] = []
    for pattern in HTML_MEDIA_RES:
        urls.extend(match.group(0) for match in pattern.finditer(html))
    og = OG_VIDEO_RE.search(html)
    if og:
        urls.append(og.group(1))
    return _dedupe_preserve_order(urls)


def extract_media_url(html: str) -> str | None:
    urls = collect_media_urls_from_html(html)
    return pick_best_media_url(urls)


def normalize_scraped_url(url: str) -> str:
    if not url:
        return url
    replacements = {
        "\\u0026": "&",
        "\\u003d": "=",
        "\\u003f": "?",
        "\\u002f": "/",
        "\\u003a": ":",
    }
    for escaped, char in replacements.items():
        if escaped in url:
            url = url.replace(escaped, char)
    return url


def pick_best_media_url(urls: list[str]) -> str | None:
    unique = _dedupe_preserve_order(url for url in urls if is_media_url(url))
    if not unique:
        return None
    for url in unique:
        lowered = url.lower()
        if ".m3u8" in lowered or "stream.mux.com" in lowered:
            return normalize_scraped_url(url)
    for url in unique:
        if ".mp4" in url.lower():
            return normalize_scraped_url(url)
    return normalize_scraped_url(unique[0])


def collapse_selenium_media_candidates(candidates: list[str]) -> list[str]:
    ordered_groups: list[str] = []
    best_by_group: dict[str, tuple[int, str]] = {}

    for raw_url in candidates:
        url = normalize_scraped_url(raw_url)
        if not _is_playable_direct_media_url(url):
            continue
        group_key = _selenium_candidate_group_key(url)
        rank = _selenium_candidate_rank(url)
        if group_key not in best_by_group:
            ordered_groups.append(group_key)
            best_by_group[group_key] = (rank, url)
            continue
        current_rank, _ = best_by_group[group_key]
        if rank < current_rank:
            best_by_group[group_key] = (rank, url)

    return [best_by_group[group_key][1] for group_key in ordered_groups]


def _is_playable_direct_media_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    if not is_media_url(lowered):
        return False
    parsed = urlparse(lowered)
    if parsed.netloc == "player.vimeo.com":
        return False
    return any(
        token in lowered
        for token in (
            ".m3u8",
            ".mp4",
            ".webm",
            "/manifest",
            "stream.mux.com",
        )
    )


def _selenium_candidate_group_key(url: str) -> str:
    lowered = url.lower()
    parsed = urlparse(url)
    if "vimeocdn.com" in parsed.netloc.lower() and ".m3u8" in lowered:
        match = UUID_PATH_SEGMENT_RE.search(parsed.path)
        if match:
            return f"vimeocdn:{match.group(1).lower()}"
    return normalize_scraped_url(url)


def _selenium_candidate_rank(url: str) -> int:
    lowered = url.lower()
    if "vimeocdn.com" in lowered and ".m3u8" in lowered:
        return 0
    if ".m3u8" in lowered or "stream.mux.com" in lowered:
        return 1
    if ".mp4" in lowered:
        return 2
    if ".webm" in lowered:
        return 3
    return 4


def _dedupe_preserve_order(urls) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def referer_for_media_url(
    media_url: str,
    *,
    page_url: str,
    current_url: str | None = None,
) -> str:
    lowered = media_url.lower()
    if "vimeocdn.com" in lowered or "vimeo.com" in lowered:
        return "https://player.vimeo.com/"
    return current_url or page_url


def build_download_headers(driver, page_url: str, media_url: str) -> dict[str, str]:
    ua = driver.execute_script("return navigator.userAgent") or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )
    referer = referer_for_media_url(
        media_url,
        page_url=page_url,
        current_url=driver.current_url,
    )
    headers = {
        "User-Agent": ua,
        "Referer": referer,
    }
    cookies = driver.get_cookies()
    if cookies:
        headers["Cookie"] = "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)
    return headers


def _create_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=options)


def _find_first(driver, selectors: tuple[str, ...]):
    for selector in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        if elements:
            return elements[0]
    return None


def _find_submit(driver):
    for selector in ("button[type='submit']", "input[type='submit']"):
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if element.is_displayed() and element.is_enabled():
                return element
    for element in driver.find_elements(By.CSS_SELECTOR, "button"):
        text = (element.text or "").lower()
        if element.is_displayed() and any(word in text for word in ("log in", "login", "sign in")):
            return element
    return None


def _looks_like_login_page(driver) -> bool:
    if _find_first(driver, EMAIL_SELECTORS) and _find_first(driver, PASSWORD_SELECTORS):
        return True
    path = urlparse(driver.current_url).path.lower()
    return any(token in path for token in ("/login", "/sign_in", "/sign-in"))


def _login_form_page(driver, login_url: str, page_url: str, username: str, password: str, log: LogFn) -> bool:
    log("[Selenium] Logging in with configured form selectors...")
    driver.get(login_url)
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email")))
    driver.find_element(By.NAME, "email").send_keys(username)
    driver.find_element(By.NAME, "password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "input[type=\"submit\"][value=\"LOGIN\"]").click()
    time.sleep(5)
    if "login" in driver.current_url.lower():
        return False
    driver.get(page_url)
    time.sleep(3)
    return True


def _login_generic(driver, page_url: str, username: str, password: str, log: LogFn) -> bool:
    log(f"[Selenium] Opening {page_url}…")
    driver.get(page_url)
    time.sleep(3)
    if not _looks_like_login_page(driver):
        log("[Selenium] No login form detected; assuming session is already authenticated.")
        return True

    log(f"[Selenium] Logging in at {driver.current_url}…")
    email = WebDriverWait(driver, 15).until(lambda d: _find_first(d, EMAIL_SELECTORS))
    password_el = _find_first(driver, PASSWORD_SELECTORS)
    if not password_el:
        log("[Selenium] Could not find password field.")
        return False

    email.clear()
    email.send_keys(username)
    password_el.clear()
    password_el.send_keys(password)

    submit = _find_submit(driver)
    if not submit:
        log("[Selenium] Could not find login submit button.")
        return False
    submit.click()
    time.sleep(5)

    page_text = driver.page_source.lower()
    if "incorrect" in page_text and "password" in page_text:
        log("[Selenium] Login failed: incorrect email or password.")
        return False
    if _looks_like_login_page(driver):
        log("[Selenium] Still on login page after submit.")
        return False

    driver.get(page_url)
    time.sleep(3)
    return True


def _wait_for_media_content(driver, timeout: int = 20) -> None:
    def _has_media_markers(d):
        html = d.page_source.lower()
        return any(
            token in html
            for token in (".m3u8", ".mp4", "og:video", "<video", "wistia", "/manifest", "stream.mux.com")
        ) or bool(d.find_elements(By.CSS_SELECTOR, "video, iframe"))

    try:
        WebDriverWait(driver, timeout).until(_has_media_markers)
    except TimeoutException:
        pass


def _collect_dom_media_urls(driver) -> list[str]:
    script = """
    const urls = [];
    document.querySelectorAll('video').forEach((video) => {
      if (video.currentSrc) urls.push(video.currentSrc);
      if (video.src) urls.push(video.src);
      video.querySelectorAll('source').forEach((source) => {
        if (source.src) urls.push(source.src);
      });
    });
    document.querySelectorAll('[data-src], [data-video-url], [data-stream-url]').forEach((el) => {
      ['data-src', 'data-video-url', 'data-stream-url'].forEach((attr) => {
        const value = el.getAttribute(attr);
        if (value) urls.push(value);
      });
    });
    return urls;
    """
    try:
        return driver.execute_script(script) or []
    except Exception:
        return []


def _collect_network_media_urls(driver, log: LogFn) -> list[str]:
    urls: list[str] = []
    try:
        for entry in driver.get_log("performance"):
            message = json.loads(entry["message"]).get("message", {})
            method = message.get("method")
            if method not in ("Network.responseReceived", "Network.requestWillBeSent"):
                continue
            params = message.get("params", {})
            url = params.get("response", {}).get("url") or params.get("request", {}).get("url")
            if url and is_media_url(url):
                urls.append(url)
    except Exception as exc:
        log(f"[Selenium] Network log scan failed: {exc}")
    return urls


def _try_click_play(driver, log: LogFn) -> None:
    selectors = (
        "button[aria-label*='Play' i]",
        "button[aria-label*='play' i]",
        ".vjs-big-play-button",
        "[data-testid*='play']",
        "button.play",
        "video",
    )
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if not element.is_displayed():
                continue
            try:
                element.click()
                log("[Selenium] Clicked a play control to start media loading.")
                time.sleep(3)
                return
            except ElementClickInterceptedException:
                try:
                    driver.execute_script("arguments[0].click();", element)
                    log("[Selenium] Clicked a play control via script.")
                    time.sleep(3)
                    return
                except Exception:
                    continue
            except Exception:
                continue


def _scan_iframe_media(driver, log: LogFn) -> list[str]:
    urls: list[str] = []
    iframes = driver.find_elements(By.CSS_SELECTOR, "iframe")
    log(f"[Selenium] Scanning {len(iframes)} iframe(s) for media URLs.")
    for iframe in iframes:
        src = iframe.get_attribute("src")
        if src and src.startswith("http"):
            urls.append(src)
        try:
            driver.switch_to.frame(iframe)
            time.sleep(2)
            urls.extend(collect_media_urls_from_html(driver.page_source))
            urls.extend(_collect_dom_media_urls(driver))
            _try_click_play(driver, log)
            time.sleep(2)
            urls.extend(collect_media_urls_from_html(driver.page_source))
            urls.extend(_collect_dom_media_urls(driver))
        except Exception:
            pass
        finally:
            driver.switch_to.default_content()
    return urls


def _prepare_page_for_media_scan(driver, log: LogFn) -> None:
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
    except Exception:
        pass
    time.sleep(1)
    _wait_for_media_content(driver)
    _try_click_play(driver, log)
    time.sleep(2)


def _find_follow_up_page_urls(driver, page_url: str) -> list[str]:
    base = urlparse(page_url)
    try:
        links = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('a[href]'))
              .map((anchor) => anchor.href)
              .filter(Boolean);
            """
        ) or []
    except Exception:
        return []

    candidates: list[str] = []
    for link in links:
        parsed = urlparse(link)
        if parsed.netloc != base.netloc:
            continue
        if link.rstrip("/") == page_url.rstrip("/"):
            continue
        path = parsed.path.lower()
        if any(token in path for token in ("/c/", "/posts/", "/lessons/", "/courses/")):
            candidates.append(link)
    return _dedupe_preserve_order(candidates)[:8]


def discover_media_urls(driver, log: LogFn, page_url: str | None = None) -> list[str]:
    _prepare_page_for_media_scan(driver, log)

    candidates: list[str] = []
    candidates.extend(collect_media_urls_from_html(driver.page_source))
    candidates.extend(_collect_dom_media_urls(driver))
    candidates.extend(_collect_network_media_urls(driver, log))
    candidates.extend(_scan_iframe_media(driver, log))
    candidates.extend(_collect_network_media_urls(driver, log))

    unique = _dedupe_preserve_order(normalize_scraped_url(url) for url in candidates if is_media_url(url))
    playable_urls = collapse_selenium_media_candidates(unique)
    if playable_urls:
        log(
            f"[Selenium] Found {len(unique)} media candidate(s); "
            f"{len(playable_urls)} playable logical video(s)."
        )
        return playable_urls

    if page_url:
        follow_ups = _find_follow_up_page_urls(driver, page_url)
        if follow_ups:
            log(f"[Selenium] No media on main page; checking {len(follow_ups)} linked page(s).")
        for link in follow_ups:
            log(f"[Selenium] Opening linked page: {link}")
            driver.get(link)
            time.sleep(3)
            media_urls = discover_media_urls(driver, log, page_url=None)
            if media_urls:
                return media_urls

    log(
        "[Selenium] No media URL found. "
        f"Page title={driver.title!r}, url={driver.current_url}"
    )
    return []


def discover_media_url(driver, log: LogFn, page_url: str | None = None) -> str | None:
    urls = discover_media_urls(driver, log, page_url=page_url)
    return pick_best_media_url(urls)


def _extract_page_title_from_driver(driver) -> str | None:
    try:
        html = driver.page_source
    except Exception:
        html = ""
    title = extract_page_title(html)
    if title:
        return title
    try:
        browser_title = (driver.title or "").strip()
    except Exception:
        browser_title = ""
    if browser_title:
        return _clean_page_title(browser_title)
    return None


def _extract_video_labels_from_driver(driver) -> list[str]:
    script = """
    const labels = [];
    const seen = new Set();
    const mediaNodes = document.querySelectorAll(
      'iframe, video, [class*="video"], [data-testid*="video"]'
    );
    mediaNodes.forEach((node) => {
      const block = node.closest('article, section, main, [class*="post"], [class*="block"], [class*="lesson"]')
        || node.parentElement;
      const heading = block?.querySelector('h1,h2,h3,h4,h5,h6,[class*="title"]');
      const text = (heading?.textContent || node.getAttribute('aria-label') || '').trim();
      if (!text || seen.has(text)) {
        return;
      }
      seen.add(text);
      labels.push(text);
    });
    return labels;
    """
    try:
        labels = driver.execute_script(script) or []
    except Exception:
        return []
    return [label.strip() for label in labels if isinstance(label, str) and label.strip()]


def _video_titles_for_urls(media_urls: list[str], labels: list[str]) -> list[str | None]:
    titles: list[str | None] = []
    for idx, media_url in enumerate(media_urls):
        label = labels[idx] if idx < len(labels) else None
        titles.append(label)
    if labels and len(labels) != len(media_urls):
        # Preserve discovered labels even when counts do not match exactly.
        for idx, label in enumerate(labels):
            if idx < len(titles) and not titles[idx]:
                titles[idx] = label
    return titles


def selenium_fetch_media_entries(
    page_url: str,
    username: str,
    password: str,
    login_plan: LoginPlan | None = None,
    log: LogFn | None = None,
) -> list[dict]:
    logger = log or (lambda _msg: None)
    logger(f"[Selenium] Starting browser fallback for {page_url}")
    driver = None
    try:
        driver = _create_driver()
        plan = login_plan or LoginPlan(login_url=f"{page_url.rstrip('/')}/login")
        if plan.login_mode == "form":
            ok = _login_form_page(driver, plan.login_url, page_url, username, password, logger)
        else:
            ok = _login_generic(driver, page_url, username, password, logger)
        if not ok:
            return []

        media_urls = discover_media_urls(driver, logger, page_url=page_url)
        if not media_urls:
            logger("[Selenium] No media URL found in page after login.")
            return []

        headers = build_download_headers(driver, page_url, media_urls[0])
        logger(f"[Selenium] Found {len(media_urls)} media URL(s).")
        for idx, media_url in enumerate(media_urls, start=1):
            logger(f" • {idx}: {media_url}")
        page_title = _extract_page_title_from_driver(driver) or title_from_page_url(page_url)
        html = driver.page_source
        video_titles = guess_video_titles(html, media_urls)
        dom_labels = _extract_video_labels_from_driver(driver)
        if dom_labels:
            video_titles = _video_titles_for_urls(media_urls, dom_labels)
        if page_title:
            logger(f"[Selenium] Page title: {page_title}")
        logger(f"[Selenium] Download Referer: {headers.get('Referer')}")
        return build_media_entries(
            media_urls,
            page_title=page_title,
            video_titles=video_titles,
            headers=headers,
        )
    except Exception as exc:
        logger(f"[Selenium] Browser fallback error: {exc}")
        return []
    finally:
        if driver:
            driver.quit()


def selenium_fetch_m3u8(
    page_url: str,
    username: str,
    password: str,
    login_plan: LoginPlan | None = None,
    log: LogFn | None = None,
) -> SeleniumMediaResult | None:
    logger = log or (lambda _msg: None)
    logger(f"[Selenium] Starting browser fallback for {page_url}")
    driver = None
    try:
        driver = _create_driver()
        plan = login_plan or LoginPlan(login_url=f"{page_url.rstrip('/')}/login")
        if plan.login_mode == "form":
            ok = _login_form_page(driver, plan.login_url, page_url, username, password, logger)
        else:
            ok = _login_generic(driver, page_url, username, password, logger)
        if not ok:
            return None

        media_url = discover_media_url(driver, logger, page_url=page_url)
        if media_url:
            headers = build_download_headers(driver, page_url, media_url)
            logger(f"[Selenium] Selected media URL: {media_url}")
            logger(f"[Selenium] Download Referer: {headers.get('Referer')}")
            return SeleniumMediaResult(media_url=media_url, headers=headers)
        logger("[Selenium] No media URL found in page after login.")
        return None
    except Exception as exc:
        logger(f"[Selenium] Browser fallback error: {exc}")
        return None
    finally:
        if driver:
            driver.quit()
