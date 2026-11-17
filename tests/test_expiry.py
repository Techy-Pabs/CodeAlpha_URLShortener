"""Link expiry: LINK_TTL_DAYS, 410 Gone, and reissuing an expired random link."""

import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app import links
from app.config import link_ttl_days
from app.extensions import db
from app.models import Link


@pytest.fixture
def ttl(app, monkeypatch):
    """Turn expiry on for one test: ttl(7) means new links live 7 days."""

    def set_ttl(days):
        monkeypatch.setitem(app.config, "LINK_TTL_DAYS", days)

    return set_ttl


def post(client, body):
    return client.post("/api/links", json=body)


def expire(code: str) -> None:
    """Move a link's expiry into the past, as if its time ran out."""
    db.session.execute(
        text("UPDATE links SET expires_at = now() - interval '1 second' WHERE code = :code"),
        {"code": code},
    )
    db.session.commit()


def row(code: str) -> Link | None:
    db.session.expire_all()
    return db.session.scalar(select(Link).where(Link.code == code))


def link_count() -> int:
    return db.session.scalar(select(func.count()).select_from(Link))


# --- the setting ------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("", None), ("  ", None), ("7", 7)])
def test_link_ttl_days_values(value, expected):
    assert link_ttl_days(value) == expected


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5"])
def test_link_ttl_days_rejects_bad_values(value):
    with pytest.raises(RuntimeError, match="LINK_TTL_DAYS"):
        link_ttl_days(value)


# --- setting expires_at ------------------------------------------------------


def test_without_ttl_links_never_expire(client):
    data = post(client, {"url": "https://example.com/forever"}).get_json()

    assert data["expires_at"] is None
    assert row(data["code"]).expires_at is None


def test_ttl_sets_expires_at_on_random_links(client, ttl):
    ttl(7)
    data = post(client, {"url": "https://example.com/week"}).get_json()

    # created_at and expires_at come from the same now() in one INSERT
    created = datetime.fromisoformat(data["created_at"])
    assert datetime.fromisoformat(data["expires_at"]) == created + timedelta(days=7)


def test_ttl_sets_expires_at_on_custom_links(client, ttl):
    ttl(2)
    data = post(client, {"url": "https://example.com", "custom_code": "two-days"}).get_json()

    created = datetime.fromisoformat(data["created_at"])
    assert datetime.fromisoformat(data["expires_at"]) == created + timedelta(days=2)


def test_live_link_with_ttl_still_redirects(client, ttl):
    ttl(7)
    code = post(client, {"url": "https://example.com/live"}).get_json()["code"]

    response = client.get(f"/{code}")

    assert response.status_code == 302
    assert row(code).clicks == 1


# --- redirecting an expired link --------------------------------------------


def test_expired_link_is_json_410(client, ttl):
    ttl(7)
    code = post(client, {"url": "https://example.com/old"}).get_json()["code"]
    expire(code)

    response = client.get(f"/{code}")

    assert response.status_code == 410
    assert "Location" not in response.headers
    assert response.get_json() == {
        "error": {"code": "link_expired", "message": f"The short link '{code}' has expired"}
    }


def test_expired_link_clicks_are_not_counted(client, ttl):
    ttl(7)
    code = post(client, {"url": "https://example.com/old"}).get_json()["code"]
    client.get(f"/{code}")
    expire(code)
    clicked_at = row(code).last_clicked_at

    client.get(f"/{code}")
    client.get(f"/{code}")

    link = row(code)
    assert link.clicks == 1
    assert link.last_clicked_at == clicked_at


def test_unknown_code_is_still_404(client):
    assert client.get("/missing1").status_code == 404


def test_stats_work_for_an_expired_link(client, ttl):
    ttl(7)
    code = post(client, {"url": "https://example.com/old"}).get_json()["code"]
    client.get(f"/{code}")
    expire(code)

    response = client.get(f"/api/links/{code}")

    assert response.status_code == 200
    data = response.get_json()
    assert data["clicks"] == 1
    assert datetime.fromisoformat(data["expires_at"]) < datetime.now().astimezone()


def test_stats_include_expires_at(client, ttl):
    ttl(7)
    created = post(client, {"url": "https://example.com/s"}).get_json()

    data = client.get(f"/api/links/{created['code']}").get_json()

    assert data["expires_at"] == created["expires_at"]


# --- shortening a URL whose random link expired ------------------------------


def test_live_random_link_is_reused(client, ttl):
    ttl(7)
    first = post(client, {"url": "https://example.com/again"})
    second = post(client, {"url": "https://example.com/again"})

    assert second.status_code == 200
    assert second.get_json() == first.get_json()


def test_expired_random_link_is_replaced_by_a_fresh_one(client, ttl):
    ttl(7)
    old = post(client, {"url": "https://example.com/again"}).get_json()
    client.get(f"/{old['code']}")
    expire(old["code"])

    response = post(client, {"url": "https://example.com/again"})

    assert response.status_code == 201
    new = response.get_json()
    assert new["code"] != old["code"]
    assert new["clicks"] == 0
    assert datetime.fromisoformat(new["created_at"]) > datetime.fromisoformat(old["created_at"])
    assert datetime.fromisoformat(new["expires_at"]) == datetime.fromisoformat(
        new["created_at"]
    ) + timedelta(days=7)
    # Same row, new identity: still one random row for the URL, as the
    # partial unique index requires. The old code now points nowhere.
    assert link_count() == 1
    assert row(new["code"]).last_clicked_at is None
    assert client.get(f"/{old['code']}").status_code == 404
    assert client.get(f"/{new['code']}").status_code == 302


def test_fresh_link_is_then_reused(client, ttl):
    ttl(7)
    old = post(client, {"url": "https://example.com/again"}).get_json()
    expire(old["code"])
    fresh = post(client, {"url": "https://example.com/again"}).get_json()

    response = post(client, {"url": "https://example.com/again"})

    assert response.status_code == 200
    assert response.get_json()["code"] == fresh["code"]


def test_reissue_after_ttl_was_switched_off_never_expires(client, ttl):
    ttl(7)
    old = post(client, {"url": "https://example.com/again"}).get_json()
    expire(old["code"])
    ttl(None)

    new = post(client, {"url": "https://example.com/again"}).get_json()

    assert new["expires_at"] is None


def test_expired_custom_link_is_left_alone(client, ttl):
    ttl(7)
    post(client, {"url": "https://example.com/c", "custom_code": "mine"})
    expire("mine")

    response = post(client, {"url": "https://example.com/c"})

    assert response.status_code == 201
    assert client.get("/mine").status_code == 410
    assert link_count() == 2


def test_code_collision_while_reissuing_is_retried(client, ttl, monkeypatch):
    ttl(7)
    post(client, {"url": "https://example.com/taken", "custom_code": "AAAAAAA"})
    old = post(client, {"url": "https://example.com/again"}).get_json()
    expire(old["code"])
    codes = iter(["AAAAAAA", "BBBBBBB"])
    monkeypatch.setattr(links, "generate_code", lambda: next(codes))

    response = post(client, {"url": "https://example.com/again"})

    assert response.status_code == 201
    assert response.get_json()["code"] == "BBBBBBB"


def test_losing_the_reissue_race_returns_the_winner(client, ttl, monkeypatch):
    # Another request reissues the row between our SELECT (which saw it
    # expired) and our UPDATE. Our UPDATE then matches nothing, and we
    # return the link the other request made.
    ttl(7)
    old = post(client, {"url": "https://example.com/race"}).get_json()
    expire(old["code"])
    real_find = links._find_random_link
    calls = []

    def find_then_lose_the_race(url):
        found = real_find(url)
        calls.append(url)
        if len(calls) == 1:
            db.session.execute(
                text(
                    "UPDATE links SET code = 'winner1', expires_at = now() + interval '7 days'"
                    " WHERE code = :code"
                ),
                {"code": old["code"]},
            )
            db.session.commit()
        return found

    monkeypatch.setattr(links, "_find_random_link", find_then_lose_the_race)

    response = post(client, {"url": "https://example.com/race"})

    assert response.status_code == 200
    assert response.get_json()["code"] == "winner1"
    assert link_count() == 1


def test_concurrent_requests_for_an_expired_url_agree_on_one_fresh_link(app, ttl, monkeypatch):
    ttl(7)
    with app.test_client() as setup_client:
        old = post(setup_client, {"url": "https://example.com/rush"}).get_json()
    expire(old["code"])

    threads_count = 8  # under the 10 per minute create limit
    # Every thread waits here after its SELECT has seen the expired link, so
    # all 8 really do try to reissue it at once. Without the "still expired"
    # check in _reissue, each would hand out its own new code.
    saw_expired = threading.Barrier(threads_count, timeout=10)
    real_find = links._find_random_link

    def find_and_wait(url):
        found = real_find(url)
        if found is not None and found[1]:
            saw_expired.wait()
        return found

    monkeypatch.setattr(links, "_find_random_link", find_and_wait)
    results = []
    errors = []

    def create():
        try:
            client = app.test_client()
            response = post(client, {"url": "https://example.com/rush"})
            results.append((response.status_code, response.get_json()["code"]))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    statuses = sorted(status for status, _ in results)
    assert statuses == [200] * (threads_count - 1) + [201]
    assert len({code for _, code in results}) == 1
    assert old["code"] not in {code for _, code in results}
    assert link_count() == 1


def test_openapi_documents_expires_at(client):
    schemas = client.get("/api/docs/openapi.json").get_json()["components"]["schemas"]

    assert "expires_at" in schemas["LinkResponse"]["properties"]
    assert "expires_at" in schemas["LinkStatsResponse"]["properties"]
