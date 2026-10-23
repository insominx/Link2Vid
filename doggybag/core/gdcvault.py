"""GDC Vault talk extraction.

yt-dlp's GDCVault extractor predates the current Blazestreaming player and
crashes on modern talk pages. Free talks expose the player iframe to anyone;
members-only talks replace it with an inline login form until the session is
authenticated through the site's plain form-POST login API.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlparse

import requests

from .extractors import (
    LogFn,
    build_media_entries,
    extract_page_title,
    resolve_blazestreaming_playback_url,
    title_from_page_url,
)

GDC_VAULT_DOMAIN = "gdcvault.com"
GDC_VAULT_ORIGIN = "https://gdcvault.com"
GDC_LOGIN_GATE_MARKER = 'id="form_login_page"'
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

SessionFactory = Callable[[], requests.Session]


class GdcVaultLoginRequired(RuntimeError):
    """The talk is members-only and no credentials were supplied."""


class GdcVaultLoginError(RuntimeError):
    """Credentials were rejected, or the account cannot open this talk."""


def is_gdcvault_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == GDC_VAULT_DOMAIN or host.endswith(f".{GDC_VAULT_DOMAIN}")


def is_login_gated(html: str) -> bool:
    return GDC_LOGIN_GATE_MARKER in html


def _entries_from_page(
    sess: requests.Session,
    url: str,
    html: str,
    headers: dict[str, str],
    log: LogFn,
) -> list[dict]:
    playback_url = resolve_blazestreaming_playback_url(sess, url, html, headers, log)
    if not playback_url:
        return []
    page_title = extract_page_title(html) or title_from_page_url(url)
    log(f"[GDC Vault] Found talk: {page_title}")
    return build_media_entries([playback_url], page_title=page_title, headers=headers)


def _login(sess: requests.Session, username: str, password: str, headers: dict[str, str], log: LogFn) -> None:
    log("[GDC Vault] Signing in …")
    response = sess.post(
        f"{GDC_VAULT_ORIGIN}/api/login.php",
        data={"email": username, "password": password},
        headers={**headers, "Referer": f"{GDC_VAULT_ORIGIN}/login"},
        timeout=20,
    )
    if response.status_code != 200:
        message = (response.text or "").strip()[:200] or f"HTTP {response.status_code}"
        raise GdcVaultLoginError(f"GDC Vault login failed: {message}")


def _logout(sess: requests.Session, headers: dict[str, str], log: LogFn) -> None:
    # GDC Vault caps concurrent sessions per account; release ours so the
    # user's browser session is not the one that gets evicted next time.
    try:
        sess.get(f"{GDC_VAULT_ORIGIN}/logout", headers=headers, timeout=15)
        log("[GDC Vault] Signed out.")
    except requests.RequestException as exc:
        log(f"[GDC Vault] Sign-out request failed: {exc}")


def extract_gdcvault_entries(
    url: str,
    username: str | None = None,
    password: str | None = None,
    *,
    log: LogFn | None = None,
    session_factory: SessionFactory | None = None,
) -> list[dict]:
    """Return the talk's stream entry, signing in only when the page is login-gated.

    Returns [] when the page has no Blazestreaming player (older talks use
    players that yt-dlp still understands).
    """
    logger = log or (lambda _msg: None)
    sess = (session_factory or requests.Session)()
    headers = {"User-Agent": _USER_AGENT, "Referer": url}

    logger("[GDC Vault] Loading talk page …")
    html = sess.get(url, headers=headers, timeout=20).text
    entries = _entries_from_page(sess, url, html, headers, logger)
    if entries or not is_login_gated(html):
        return entries

    if not (username and password):
        raise GdcVaultLoginRequired("This GDC Vault talk is members-only. Sign in with your GDC Vault account.")

    _login(sess, username, password, headers, logger)
    try:
        html = sess.get(url, headers=headers, timeout=20).text
        entries = _entries_from_page(sess, url, html, headers, logger)
        if entries:
            return entries
        if is_login_gated(html):
            raise GdcVaultLoginError(
                "GDC Vault accepted the login, but this talk is still locked. "
                "The account may not include members-only access."
            )
        return []
    finally:
        _logout(sess, headers, logger)
