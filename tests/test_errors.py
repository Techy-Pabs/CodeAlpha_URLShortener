"""Flask's own error pages come back in our JSON shape too."""

import logging

from app import create_app


def test_unknown_api_route_is_json_404(client):
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.content_type == "application/json"
    error = response.get_json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]


def test_wrong_method_is_json_405_with_allow_header(client):
    response = client.get("/api/links")

    assert response.status_code == 405
    assert response.content_type == "application/json"
    assert response.get_json()["error"]["code"] == "method_not_allowed"
    assert "POST" in response.headers["Allow"]


def test_unhandled_exception_is_json_500(caplog):
    # A separate app, because routes can't be added once the shared one
    # has served a request
    app = create_app("testing")

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals")

    with caplog.at_level(logging.ERROR):
        response = app.test_client().get("/boom")

    assert response.status_code == 500
    assert response.get_json() == {
        "error": {
            "code": "internal_server_error",
            "message": "Something went wrong on our side.",
        }
    }
    assert "secret internals" not in response.get_data(as_text=True)
    assert "Unhandled error" in caplog.text
