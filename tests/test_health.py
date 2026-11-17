from sqlalchemy.exc import OperationalError

from app.extensions import db


def test_health_returns_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_health_returns_503_when_database_is_down(client, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(db.session, "execute", fail)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.get_json() == {"status": "unavailable"}
