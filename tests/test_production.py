"""Production settings, security headers, the Neon URL and gunicorn.conf.py."""

import runpy
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app import create_app
from app.config import ProductionConfig, database_uri
from app.extensions import db

ROOT = Path(__file__).resolve().parents[1]

NEON_URL = (
    "postgresql://user:pw@ep-example-123.eu-central-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


@pytest.fixture
def production(monkeypatch):
    """Production config with every required value set; tests remove some."""
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "prod-test-key")
    monkeypatch.setattr(ProductionConfig, "SQLALCHEMY_DATABASE_URI", database_uri(NEON_URL))
    monkeypatch.setattr(ProductionConfig, "BASE_URL", "https://sho.example")
    return monkeypatch


# --- required settings -------------------------------------------------------


def test_production_starts_with_everything_set(production):
    app = create_app("production")
    assert app.config["DEBUG"] is False
    assert app.config["BASE_URL"] == "https://sho.example"


@pytest.mark.parametrize(
    ("key", "env_name"),
    [
        ("SECRET_KEY", "SECRET_KEY"),
        ("SQLALCHEMY_DATABASE_URI", "DATABASE_URL"),
        ("BASE_URL", "BASE_URL"),
    ],
)
@pytest.mark.parametrize("value", [None, ""])
def test_production_refuses_to_start_without(production, key, env_name, value):
    production.setattr(ProductionConfig, key, value)

    with pytest.raises(RuntimeError, match=f"production: {env_name}"):
        create_app("production")


def test_all_missing_settings_are_named_at_once(production):
    production.setattr(ProductionConfig, "SECRET_KEY", None)
    production.setattr(ProductionConfig, "BASE_URL", None)

    with pytest.raises(RuntimeError, match="SECRET_KEY, BASE_URL"):
        create_app("production")


# --- Neon -------------------------------------------------------------------


def test_neon_url_uses_psycopg_and_keeps_ssl_options():
    url = database_uri(NEON_URL)
    assert url.startswith("postgresql+psycopg://")

    engine = create_engine(url)
    _, params = engine.dialect.create_connect_args(engine.url)
    assert params["sslmode"] == "require"
    assert params["channel_binding"] == "require"
    assert params["host"] == "ep-example-123.eu-central-1.aws.neon.tech"
    engine.dispose()


def test_production_engine_pings_pooled_connections(production):
    # Neon closes connections that sit idle. pool_pre_ping tests each pooled
    # connection before use and quietly replaces a dead one.
    app = create_app("production")
    with app.app_context():
        engine = db.engine
        assert engine.pool._pre_ping is True
        assert engine.url.query["sslmode"] == "require"


# --- security headers --------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/api/health", "/api/docs/", "/", "/no-such-code", "/api/links/missing"],
)
def test_security_headers_on_every_response(client, path):
    response = client.get(path)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_security_headers_on_errors_and_redirects(client):
    code = client.post("/api/links", json={"url": "https://example.com"}).get_json()["code"]
    for response in (
        client.get(f"/{code}"),  # 302
        client.post("/api/links", json={"url": "nope"}),  # 400
        client.delete("/api/health"),  # 405
    ):
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"


# --- gunicorn.conf.py --------------------------------------------------------


def gunicorn_conf(monkeypatch, **env):
    for name in ("PORT", "WEB_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return runpy.run_path(str(ROOT / "gunicorn.conf.py"))


def test_gunicorn_defaults(monkeypatch):
    conf = gunicorn_conf(monkeypatch)
    assert conf["bind"] == "0.0.0.0:8000"
    assert conf["workers"] == 1
    assert conf["accesslog"] == "-"


def test_gunicorn_uses_port_and_web_concurrency(monkeypatch):
    conf = gunicorn_conf(monkeypatch, PORT="10000", WEB_CONCURRENCY="3")
    assert conf["bind"] == "0.0.0.0:10000"
    assert conf["workers"] == 3


@pytest.mark.parametrize("name", ["PORT", "WEB_CONCURRENCY"])
@pytest.mark.parametrize("value", ["abc", "0", "-1"])
def test_gunicorn_rejects_bad_numbers(monkeypatch, name, value):
    with pytest.raises(SystemExit, match=name):
        gunicorn_conf(monkeypatch, **{name: value})
