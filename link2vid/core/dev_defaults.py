"""Helpers for optional developer.json defaults."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class LoginPlan:
    login_url: str
    login_mode: str = "generic"


def normalize_domain(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = urlparse(text).netloc or text
    if text.startswith("www."):
        text = text[4:]
    return text.split(":")[0]


def host_matches_domain(host: str, domain: str) -> bool:
    normalized_host = normalize_domain(host)
    normalized_domain = normalize_domain(domain)
    if not normalized_host or not normalized_domain:
        return False
    return normalized_host == normalized_domain or normalized_host.endswith(f".{normalized_domain}")


def credential_entries(dev_defaults: dict | None) -> list[dict]:
    if not dev_defaults:
        return []

    sites = dev_defaults.get("sites")
    if isinstance(sites, list):
        return [entry for entry in sites if isinstance(entry, dict)]

    domain = dev_defaults.get("default_domain") or dev_defaults.get("domain")
    if not domain:
        return []
    return [
        {
            "domain": domain,
            "username": dev_defaults.get("default_username"),
            "password": dev_defaults.get("default_password"),
        }
    ]


def dev_domain(dev_defaults: dict | None) -> str:
    entries = credential_entries(dev_defaults)
    if not entries:
        return ""
    domain = entries[0].get("domain") or entries[0].get("default_domain")
    return normalize_domain(str(domain) if domain else "")


def dev_domain_for_url(url: str, dev_defaults: dict | None) -> str:
    host = urlparse(url).netloc
    for entry in credential_entries(dev_defaults):
        domain = entry.get("domain") or entry.get("default_domain")
        if domain and host_matches_domain(host, str(domain)):
            return normalize_domain(str(domain))
    return ""


def url_matches_dev_domain(url: str, dev_defaults: dict | None) -> bool:
    return bool(dev_domain_for_url(url, dev_defaults))


def dev_credentials_for_url(url: str, dev_defaults: dict | None) -> tuple[str | None, str | None]:
    if not dev_defaults or not dev_defaults.get("use_defaults"):
        return None, None

    host = urlparse(url).netloc
    for entry in credential_entries(dev_defaults):
        domain = entry.get("domain") or entry.get("default_domain")
        if not domain or not host_matches_domain(host, str(domain)):
            continue
        username = entry.get("username") or entry.get("default_username")
        password = entry.get("password") or entry.get("default_password")
        if not username or not password:
            return None, None
        return str(username), str(password)
    return None, None


def resolve_login_plan(url: str, dev_defaults: dict | None) -> LoginPlan:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else url.rstrip("/")
    default_login_url = f"{origin}/login"

    host = parsed.netloc
    for entry in credential_entries(dev_defaults):
        domain = entry.get("domain") or entry.get("default_domain")
        if not domain or not host_matches_domain(host, str(domain)):
            continue
        login_url = str(entry.get("login_url") or default_login_url)
        login_mode = str(entry.get("login_mode") or "generic").strip().lower()
        if login_mode not in {"generic", "form"}:
            login_mode = "generic"
        return LoginPlan(login_url=login_url, login_mode=login_mode)

    return LoginPlan(login_url=default_login_url)
