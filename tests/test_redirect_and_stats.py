"""GET /<code> and GET /api/links/<code>."""

import threading
from datetime import datetime

from sqlalchemy import select

from app.extensions import db
from app.models import Link


def make_link(client, url="https://example.com/target?q=1", custom_code=None) -> dict:
    body = {"url": url} if custom_code is None else {"url": url, "custom_code": custom_code}
    response = client.post("/api/links", json=body)
    assert response.status_code == 201
    return response.get_json()


def stored(code: str) -> tuple[int, datetime | None]:
    return db.session.execute(
        select(Link.clicks, Link.last_clicked_at).where(Link.code == code)
    ).one()


# --- redirect ---------------------------------------------------------------


def test_redirect_is_302_to_the_original_url(client):
    link = make_link(client, "https://example.com/Some/Path?x=1#frag")

    response = client.get(f"/{link['code']}")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://example.com/Some/Path?x=1#frag"


def test_redirect_counts_the_click_and_sets_last_clicked_at(client):
    link = make_link(client)
    assert stored(link["code"]) == (0, None)

    client.get(f"/{link['code']}")
    clicks, first_click = stored(link["code"])
    assert clicks == 1
    assert first_click is not None
    assert first_click >= datetime.fromisoformat(link["created_at"])

    client.get(f"/{link['code']}")
    clicks, second_click = stored(link["code"])
    assert clicks == 2
    assert second_click > first_click


def test_redirect_only_counts_its_own_link(client):
    a = make_link(client, "https://example.com/a")
    b = make_link(client, "https://example.com/b")

    client.get(f"/{a['code']}")

    assert stored(a["code"])[0] == 1
    assert stored(b["code"]) == (0, None)


def test_unknown_code_is_json_404(client):
    response = client.get("/nope123")

    assert response.status_code == 404
    assert response.get_json() == {
        "error": {"code": "not_found", "message": "No short link with the code 'nope123'"}
    }


def test_codes_are_case_sensitive(client):
    make_link(client, custom_code="CaseCode")

    assert client.get("/CaseCode").status_code == 302
    assert client.get("/casecode").status_code == 404


def test_concurrent_clicks_are_all_counted(app):
    with app.test_client() as setup_client:
        code = make_link(setup_client)["code"]

    threads_count = 20
    barrier = threading.Barrier(threads_count)
    statuses = []
    errors = []

    def click():
        try:
            client = app.test_client()
            barrier.wait()  # release all 20 requests at once
            statuses.append(client.get(f"/{code}").status_code)
        except Exception as exc:  # surface thread failures in the test
            errors.append(exc)

    threads = [threading.Thread(target=click) for _ in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert statuses == [302] * threads_count
    assert stored(code)[0] == threads_count


# --- stats ------------------------------------------------------------------


def test_stats_for_a_new_link(client):
    link = make_link(client, "https://example.com/stats")

    response = client.get(f"/api/links/{link['code']}")

    assert response.status_code == 200
    assert response.get_json() == {
        "code": link["code"],
        "short_url": f"http://localhost/{link['code']}",
        "original_url": "https://example.com/stats",
        "clicks": 0,
        "created_at": link["created_at"],
        "last_clicked_at": None,
        "is_custom": False,
    }


def test_stats_after_clicks(client):
    link = make_link(client, custom_code="stat-me")
    client.get("/stat-me")
    client.get("/stat-me")

    data = client.get("/api/links/stat-me").get_json()

    assert data["clicks"] == 2
    assert data["is_custom"] is True
    assert datetime.fromisoformat(data["last_clicked_at"]) >= datetime.fromisoformat(
        link["created_at"]
    )


def test_viewing_stats_is_not_a_click(client):
    link = make_link(client)
    client.get(f"/api/links/{link['code']}")

    assert stored(link["code"]) == (0, None)


def test_stats_for_unknown_code_is_json_404(client):
    response = client.get("/api/links/missing")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"
