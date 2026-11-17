"""Swagger docs, the home redirect, rate limiting and ProxyFix."""

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

from app import create_app
from app.config import TestingConfig, proxy_count


def create(client, n, **kwargs):
    return client.post("/api/links", json={"url": f"https://example.com/{n}"}, **kwargs)


# --- docs and home ----------------------------------------------------------


def test_swagger_ui_loads_at_api_docs(client):
    response = client.get("/api/docs", follow_redirects=True)

    assert response.status_code == 200
    assert response.request.path == "/api/docs/"
    html = response.get_data(as_text=True)
    assert "swagger-ui" in html
    assert "openapi.json" in html


def test_swagger_assets_load(client):
    response = client.get("/api/docs/swagger/js/swagger-ui-bundle.js")
    assert response.status_code == 200


def test_openapi_spec_is_built_from_the_pydantic_models(client):
    response = client.get("/api/docs/openapi.json")

    assert response.status_code == 200
    spec = response.get_json()
    assert spec["info"]["title"] == "URL Shortener API"
    assert set(spec["paths"]) == {"/api/health", "/api/links", "/api/links/{code}"}

    create_op = spec["paths"]["/api/links"]["post"]
    assert {"201", "200", "400", "409", "429"} <= set(create_op["responses"])
    schemas = spec["components"]["schemas"]
    assert set(schemas["CreateLinkRequest"]["properties"]) == {"url", "custom_code"}
    assert schemas["CreateLinkRequest"]["required"] == ["url"]
    assert "last_clicked_at" in schemas["LinkStatsResponse"]["properties"]
    assert "ErrorResponse" in schemas


def test_home_redirects_to_docs(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"] == "/api/docs/"


# --- rate limiting ----------------------------------------------------------


def test_eleventh_create_in_a_minute_is_429(client):
    for n in range(10):
        assert create(client, n).status_code == 201

    response = create(client, 10)

    assert response.status_code == 429
    assert response.content_type == "application/json"
    error = response.get_json()["error"]
    assert error["code"] == "too_many_requests"
    assert "10 per minute" in error["message"]
    assert set(response.get_json()) == {"error"}
    assert int(response.headers["Retry-After"]) > 0


def test_limit_is_per_client_ip(client):
    for n in range(10):
        create(client, n, environ_base={"REMOTE_ADDR": "10.0.0.1"})
    assert create(client, 10, environ_base={"REMOTE_ADDR": "10.0.0.1"}).status_code == 429

    assert create(client, 11, environ_base={"REMOTE_ADDR": "10.0.0.2"}).status_code == 201


def test_limit_only_applies_to_creating(client):
    code = create(client, 0).get_json()["code"]
    for n in range(1, 10):
        create(client, n)

    for _ in range(15):
        assert client.get(f"/api/links/{code}").status_code == 200
        assert client.get(f"/{code}").status_code == 302


def test_limiter_is_cleared_between_tests_part_1(client):
    for n in range(10):
        assert create(client, n).status_code == 201


def test_limiter_is_cleared_between_tests_part_2(client):
    # Would be the 11th request if the counters carried over from part 1
    assert create(client, 0).status_code == 201


# --- proxies ----------------------------------------------------------------


def test_forwarded_for_is_ignored_without_trusted_proxies(app, client):
    # NUM_PROXIES defaults to 0, so a client can't dodge the limit by
    # sending a made-up X-Forwarded-For
    assert not isinstance(app.wsgi_app, ProxyFix)
    for n in range(10):
        create(client, n, headers={"X-Forwarded-For": f"203.0.113.{n}"})

    response = create(client, 10, headers={"X-Forwarded-For": "203.0.113.99"})
    assert response.status_code == 429


def test_behind_one_proxy_the_forwarded_ip_is_the_client(monkeypatch):
    monkeypatch.setattr(TestingConfig, "NUM_PROXIES", 1)
    proxied = create_app("testing")
    assert isinstance(proxied.wsgi_app, ProxyFix)
    client = proxied.test_client()

    for n in range(10):
        create(client, n, headers={"X-Forwarded-For": "198.51.100.1"})
    assert create(client, 10, headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429
    # Same proxy address (127.0.0.1), different client behind it
    assert create(client, 11, headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 201


@pytest.mark.parametrize(("value", "expected"), [(None, 0), ("", 0), ("0", 0), ("2", 2)])
def test_num_proxies_parsing(value, expected):
    assert proxy_count(value) == expected


@pytest.mark.parametrize("value", ["abc", "1.5", "-1"])
def test_bad_num_proxies_is_rejected(value):
    with pytest.raises(RuntimeError, match="NUM_PROXIES"):
        proxy_count(value)
