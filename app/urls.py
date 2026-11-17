"""Rules for the URLs and codes users send. Pure functions, no Flask or database."""

import re
from urllib.parse import urlsplit, urlunsplit

from app.models import CODE_MAX_LENGTH, URL_MAX_LENGTH

CODE_MIN_LENGTH = 3
CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
# Paths the app uses (or may use) itself, compared case-insensitively
RESERVED_CODES = frozenset({"api", "docs", "health", "static", "admin"})

_WHITESPACE_OR_CONTROL = re.compile(r"[\s\x00-\x1f\x7f]")


def normalize_url(raw: str) -> str:
    """Return the cleaned URL, or raise ValueError saying what is wrong.

    Trims whitespace, requires http(s) and a host, and lowercases the scheme
    and host. The path, query and fragment are kept exactly as sent, because
    servers may treat /Page and /page as different pages.
    """
    url = raw.strip()
    if not url:
        raise ValueError("URL must not be empty")
    if len(url) > URL_MAX_LENGTH:
        raise ValueError(f"URL must be at most {URL_MAX_LENGTH} characters")
    # Postgres can't index a value over ~2.7 KB, and non-ASCII characters
    # take 2-4 bytes each, so cap the encoded size too.
    if len(url.encode()) > URL_MAX_LENGTH:
        raise ValueError(f"URL must be at most {URL_MAX_LENGTH} bytes once UTF-8 encoded")
    if _WHITESPACE_OR_CONTROL.search(url):
        raise ValueError("URL must not contain spaces or control characters")

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    host = parts.hostname  # already lowercased, without IPv6 brackets
    if not host:
        raise ValueError("URL must include a host, e.g. https://example.com")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("URL has an invalid port") from None

    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    if "@" in parts.netloc:
        netloc = parts.netloc.rpartition("@")[0] + "@" + netloc
    return urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment))


def url_host(url: str) -> str | None:
    return urlsplit(url).hostname


def check_custom_code(code: str) -> str:
    """Return the code unchanged, or raise ValueError saying what is wrong."""
    if not CODE_MIN_LENGTH <= len(code) <= CODE_MAX_LENGTH:
        raise ValueError(
            f"custom code must be {CODE_MIN_LENGTH} to {CODE_MAX_LENGTH} characters long"
        )
    if not CODE_PATTERN.fullmatch(code):
        raise ValueError(
            "custom code may only contain letters, digits, hyphens (-) and underscores (_)"
        )
    if code.lower() in RESERVED_CODES:
        raise ValueError(f"'{code}' is reserved; choose another custom code")
    return code
