"""Email quality checks (email enrichment cleanup, see
config/email_denylist.yml): placeholder/developer-tooling denylisting,
free-mail recognition, business-domain agreement, MX record lookup.
Config-driven -- not hardcoded -- so lists can be edited without a code
change."""
from __future__ import annotations

import fnmatch
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
EMAIL_DENYLIST_PATH = ROOT / "config" / "email_denylist.yml"

_MX_CACHE: dict[str, bool] = {}


@lru_cache(maxsize=1)
def _load_config() -> dict:
    return yaml.safe_load(EMAIL_DENYLIST_PATH.read_text()) or {}


def _root_domain(value: str) -> str | None:
    """Root domain (no www/protocol/path/port) from a URL, host, or the
    domain portion of an email address."""
    if not value:
        return None
    value = value.strip()
    if "@" in value and "://" not in value:
        value = value.rsplit("@", 1)[-1]
    if "://" not in value:
        value = "//" + value
    host = urlparse(value).netloc or urlparse(value).path
    host = host.split("/")[0].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def is_denylisted_email(email: str | None) -> bool:
    """True if the email's local-part matches a known placeholder prefix
    (sample/example/test/... before @) or its domain is a known developer/
    error-tracking/website-builder domain (config/email_denylist.yml)."""
    if not email or "@" not in email:
        return False
    local_part, domain = email.strip().lower().split("@", 1)
    cfg = _load_config()

    for prefix in cfg.get("placeholder_local_parts", []):
        if local_part.startswith(prefix.lower()):
            return True

    domain = domain.split("/")[0]
    for deny_domain in cfg.get("denylist_domains", []):
        deny_domain = deny_domain.lower()
        if domain == deny_domain or domain.endswith("." + deny_domain):
            return True

    for pattern in cfg.get("denylist_domain_patterns", []):
        if fnmatch.fnmatch(domain, pattern.lower()):
            return True

    return False


def free_mail_domains() -> set[str]:
    cfg = _load_config()
    return {d.lower() for d in cfg.get("free_mail_domains", [])}


def domain_agrees(email: str | None, business_website: str | None) -> bool:
    """True if the email's domain matches the business website's root
    domain, or the email is on a free-mail provider (always agrees)."""
    if not email or "@" not in email:
        return False
    email_domain = _root_domain(email)
    if not email_domain:
        return False
    if email_domain in free_mail_domains():
        return True
    website_domain = _root_domain(business_website) if business_website else None
    return bool(website_domain) and email_domain == website_domain


def _resolve_mx(domain: str) -> bool:  # pragma: no cover - real DNS I/O
    import dns.resolver

    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5.0)
        return len(answers) > 0
    except Exception:
        return False


def has_mx_record(domain: str) -> bool:
    """Real DNS MX lookup, cached in-memory per domain for the run."""
    if not domain:
        return False
    domain = domain.strip().lower()
    if domain in _MX_CACHE:
        return _MX_CACHE[domain]
    result = _resolve_mx(domain)
    _MX_CACHE[domain] = result
    return result
