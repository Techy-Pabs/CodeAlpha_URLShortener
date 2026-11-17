"""POST /api/links."""

import logging
import re
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app import links
from app.extensions import db
from app.models import Link

BASE62_7 = re.compile(r"[0-9A-Za-z]{7}")


def post(client, body):
    return client.post("/api/links", json=body)


def link_count() -> int:
    return db.session.scalar(select(func.count()).select_from(Link))


def assert_validation_error(response, field, message_part):
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == "validation_error"
    assert message_part in error["message"]
    assert any(d["field"] == field and message_part in d["message"] for d in error["details"])


# --- creating ---------------------------------------------------------------


def test_create_random_link(client):
    response = post(client, {"url": "https://example.com/some/long/path"})

    assert response.status_code == 201
    data = response.get_json()
    assert set(data) == {"code", "short_url", "original_url", "clicks", "created_at"}
    assert BASE62_7.fullmatch(data["code"])
    assert data["short_url"] == f"http://localhost/{data['code']}"
    assert data["original_url"] == "https://example.com/some/long/path"
    assert data["clicks"] == 0
    assert datetime.fromisoformat(data["created_at"]).tzinfo is not None

    link = db.session.scalar(select(Link).where(Link.code == data["code"]))
    assert link.is_custom is False


def test_create_custom_link(client):
    response = post(client, {"url": "https://example.com", "custom_code": "My_Link-1"})

    assert response.status_code == 201
    data = response.get_json()
    assert data["code"] == "My_Link-1"
    assert data["short_url"] == "http://localhost/My_Link-1"
    assert db.session.scalar(select(Link.is_custom).where(Link.code == "My_Link-1")) is True


def test_stored_url_is_normalized(client):
    response = post(client, {"url": "  HTTPS://Example.COM/Keep/This?Case=Yes  "})

    assert response.status_code == 201
    assert response.get_json()["original_url"] == "https://example.com/Keep/This?Case=Yes"


def test_random_codes_differ(client):
    codes = {post(client, {"url": f"https://example.com/{i}"}).get_json()["code"] for i in range(5)}
    assert len(codes) == 5


def test_codes_come_from_secrets(monkeypatch):
    calls = []
    monkeypatch.setattr(links.secrets, "choice", lambda seq: calls.append(seq) or "x")

    assert links.generate_code() == "xxxxxxx"
    assert len(calls) == 7
    assert calls[0] == links.BASE62
    assert len(links.BASE62) == 62


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://example.com", "must start with http:// or https://"),
        ("example.com", "must start with http:// or https://"),
        ("https://", "must include a host"),
        ("   ", "must not be empty"),
        ("https://example.com/" + "a" * 2029, "at most 2048 characters"),
    ],
)
def test_bad_urls_return_400(client, url, message):
    assert_validation_error(post(client, {"url": url}), "url", message)
    assert link_count() == 0


@pytest.mark.parametrize(
    "url",
    ["http://localhost/abc", "https://LOCALHOST:9999/x", "http://localhost"],
)
def test_url_on_our_own_host_is_rejected(client, url):
    # TestingConfig.BASE_URL is http://localhost
    assert_validation_error(post(client, {"url": url}), "url", "must not point at this shortener")
    assert link_count() == 0


def test_missing_url(client):
    assert_validation_error(post(client, {}), "url", "Field required")


def test_url_must_be_a_string(client):
    assert_validation_error(post(client, {"url": 123}), "url", "valid string")


def test_unknown_fields_are_rejected(client):
    response = post(client, {"url": "https://example.com", "customCode": "abc"})
    assert_validation_error(response, "customCode", "Extra inputs are not permitted")


@pytest.mark.parametrize("body", ["not json", "[1, 2]", '"https://example.com"'])
def test_body_must_be_a_json_object(client, body):
    response = client.post("/api/links", data=body, content_type="application/json")

    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == "validation_error"
    assert "JSON object" in error["message"]


def test_missing_body(client):
    response = client.post("/api/links")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("ab", "3 to 30 characters"),
        ("a" * 31, "3 to 30 characters"),
        ("no spaces", "only contain letters"),
        ("no/slash", "only contain letters"),
    ],
)
def test_bad_custom_codes_return_400(client, code, message):
    response = post(client, {"url": "https://example.com", "custom_code": code})
    assert_validation_error(response, "custom_code", message)


@pytest.mark.parametrize("code", ["api", "Docs", "HEALTH", "static", "Admin"])
def test_reserved_custom_codes_return_400(client, code):
    response = post(client, {"url": "https://example.com", "custom_code": code})
    assert_validation_error(response, "custom_code", "reserved")


def test_several_problems_are_all_reported(client):
    response = post(client, {"url": "ftp://x", "custom_code": "a"})

    assert response.status_code == 400
    fields = {d["field"] for d in response.get_json()["error"]["details"]}
    assert fields == {"url", "custom_code"}


# --- conflicts and duplicates -----------------------------------------------


def test_taken_custom_code_returns_409(client):
    post(client, {"url": "https://example.com/1", "custom_code": "taken"})

    response = post(client, {"url": "https://example.com/2", "custom_code": "taken"})

    assert response.status_code == 409
    assert response.get_json() == {
        "error": {"code": "code_taken", "message": "The code 'taken' is already taken"}
    }
    assert link_count() == 1


def test_custom_code_matching_a_random_code_returns_409(client):
    random_code = post(client, {"url": "https://example.com"}).get_json()["code"]

    response = post(client, {"url": "https://example.com/x", "custom_code": random_code})

    assert response.status_code == 409


def test_same_url_without_custom_code_returns_existing_link(client):
    first = post(client, {"url": "https://example.com/dup"})
    second = post(client, {"url": "https://example.com/dup"})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json() == first.get_json()
    assert link_count() == 1


def test_duplicate_check_uses_the_normalized_url(client):
    first = post(client, {"url": "https://example.com/dup"})
    second = post(client, {"url": "  HTTPS://EXAMPLE.com/dup "})

    assert second.status_code == 200
    assert second.get_json()["code"] == first.get_json()["code"]


def test_duplicate_check_keeps_path_case(client):
    first = post(client, {"url": "https://example.com/Page"})
    second = post(client, {"url": "https://example.com/page"})

    assert second.status_code == 201
    assert second.get_json()["code"] != first.get_json()["code"]


def test_custom_codes_for_the_same_url_are_separate_links(client):
    random = post(client, {"url": "https://example.com"})
    custom1 = post(client, {"url": "https://example.com", "custom_code": "one"})
    custom2 = post(client, {"url": "https://example.com", "custom_code": "two"})

    assert [r.status_code for r in (random, custom1, custom2)] == [201, 201, 201]
    assert link_count() == 3


def test_custom_link_is_not_reused_for_a_random_request(client):
    post(client, {"url": "https://example.com", "custom_code": "mine"})

    response = post(client, {"url": "https://example.com"})

    assert response.status_code == 201
    assert response.get_json()["code"] != "mine"


def test_race_on_same_url_returns_the_link_that_won(client, monkeypatch):
    # Simulate a second request that committed the same URL between our
    # "does it exist?" SELECT and our INSERT: the SELECT says no, the
    # unique index then says yes.
    winner = post(client, {"url": "https://example.com/race"}).get_json()

    real_find = links._find_random_link
    finds = []

    def find_misses_first_time(url):
        finds.append(url)
        return None if len(finds) == 1 else real_find(url)

    broken = []
    real_broken = links._broken_constraint
    monkeypatch.setattr(links, "_find_random_link", find_misses_first_time)
    monkeypatch.setattr(
        links, "_broken_constraint", lambda e: broken.append(real_broken(e)) or broken[-1]
    )

    response = post(client, {"url": "https://example.com/race"})

    assert response.status_code == 200
    assert response.get_json()["code"] == winner["code"]
    assert broken == ["uq_links_original_url_random"]  # the INSERT really hit the index
    assert len(finds) == 2
    assert link_count() == 1


# --- random code collisions -------------------------------------------------


def test_collision_is_retried(client, monkeypatch, caplog):
    taken = post(client, {"url": "https://example.com/1", "custom_code": "AAAAAAA"})
    assert taken.status_code == 201

    codes = iter(["AAAAAAA", "AAAAAAA", "BBBBBBB"])
    monkeypatch.setattr(links, "generate_code", lambda: next(codes))

    with caplog.at_level(logging.WARNING):
        response = post(client, {"url": "https://example.com/2"})

    assert response.status_code == 201
    assert response.get_json()["code"] == "BBBBBBB"
    assert caplog.text.count("Random code collision") == 2


def test_gives_up_after_five_collisions(client, monkeypatch, caplog):
    post(client, {"url": "https://example.com/1", "custom_code": "AAAAAAA"})
    attempts = []
    monkeypatch.setattr(links, "generate_code", lambda: attempts.append(1) or "AAAAAAA")

    with caplog.at_level(logging.WARNING):
        response = post(client, {"url": "https://example.com/2"})

    assert response.status_code == 500
    assert response.get_json() == {
        "error": {
            "code": "code_generation_failed",
            "message": "Could not generate a unique short code. Please try again.",
        }
    }
    assert len(attempts) == 5
    assert "Could not generate a unique short code after 5 attempts" in caplog.text
    assert link_count() == 1
