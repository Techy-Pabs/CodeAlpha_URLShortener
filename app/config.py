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
    REQUIRED = {**Config.REQUIRED, "SQLALCHEMY_DATABASE_URI": "TEST_DATABASE_URL"}


class ProductionConfig(Config):
    DEBUG = False


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
