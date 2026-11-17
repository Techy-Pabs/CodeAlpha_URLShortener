"""Settings for each environment, read from environment variables (.env locally)."""

import os

from dotenv import load_dotenv

# Load .env into os.environ. Variables that are already set (for example in
# CI or on the host) win over the file.
load_dotenv()


def database_uri(url: str | None) -> str | None:
    """Point SQLAlchemy at the psycopg 3 driver.

    Hosts such as Render hand out postgres:// or postgresql:// URLs, which
    SQLAlchemy would read as "use psycopg2". We installed psycopg 3.
    """
    if not url:
        return url
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def proxy_count(value: str | None) -> int:
    """NUM_PROXIES: how many reverse proxies sit in front of the app (Render: 1)."""
    try:
        count = int(value or "0")
    except ValueError:
        raise RuntimeError(f"NUM_PROXIES must be a whole number, got {value!r}") from None
    if count < 0:
        raise RuntimeError(f"NUM_PROXIES must not be negative, got {count}")
    return count


def link_ttl_days(value: str | None) -> int | None:
    """LINK_TTL_DAYS: days a new link lives. Empty or unset means forever."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        days = int(raw)
    except ValueError:
        raise RuntimeError(f"LINK_TTL_DAYS must be a whole number of days, got {raw!r}") from None
    if days < 1:
        raise RuntimeError(f"LINK_TTL_DAYS must be at least 1 (or empty for no expiry), got {days}")
    return days


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = database_uri(os.environ.get("DATABASE_URL"))
    # The public address short links are built from, e.g. https://sho.rt
    BASE_URL = os.environ.get("BASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS = {
        # Check a pooled connection still works before handing it out
        "pool_pre_ping": True,
        # Give up after 5 seconds if the database can't be reached, so the
        # health check answers 503 instead of hanging
        "connect_args": {"connect_timeout": 5},
    }
    # Trust this many X-Forwarded-For / X-Forwarded-Proto hops. 0 (the
    # default) trusts none, so a client can't fake its IP to dodge limits.
    NUM_PROXIES = proxy_count(os.environ.get("NUM_PROXIES"))
    # New links expire this many days after they're created. None: never.
    LINK_TTL_DAYS = link_ttl_days(os.environ.get("LINK_TTL_DAYS"))
    # Flask-Limiter: counters live in this process's memory, so they reset on
    # restart and aren't shared between workers. Fine for one instance.
    RATELIMIT_STORAGE_URI = "memory://"
    # Send X-RateLimit-* and Retry-After headers so clients know when to retry
    RATELIMIT_HEADERS_ENABLED = True
    # Settings create_app refuses to start without: {config key: env variable}
    REQUIRED = {
        "SECRET_KEY": "SECRET_KEY",
        "SQLALCHEMY_DATABASE_URI": "DATABASE_URL",
        "BASE_URL": "BASE_URL",
    }


class DevelopmentConfig(Config):
    DEBUG = True
    BASE_URL = os.environ.get("BASE_URL", "http://localhost:8002")


class TestingConfig(Config):
    TESTING = True
    # Tests never sign anything that leaves the machine
    SECRET_KEY = os.environ.get("SECRET_KEY", "test")
    # A separate database, so tests can create and drop tables freely
    SQLALCHEMY_DATABASE_URI = database_uri(os.environ.get("TEST_DATABASE_URL"))
    BASE_URL = "http://localhost"
    # Tests that need expiry switch it on themselves
    LINK_TTL_DAYS = None
    REQUIRED = {**Config.REQUIRED, "SQLALCHEMY_DATABASE_URI": "TEST_DATABASE_URL"}


class ProductionConfig(Config):
    DEBUG = False


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
