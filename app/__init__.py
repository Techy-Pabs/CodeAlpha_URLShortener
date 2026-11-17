"""App factory: create_app builds a configured Flask app."""

import os

from flask import Flask
from flask_openapi3 import Info, OpenAPI
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import CONFIGS
from app.errors import pydantic_error_response, register_error_handlers
from app.extensions import db, limiter, migrate

DOCS_PREFIX = "/api/docs"


def create_app(config_name: str | None = None) -> Flask:
    """Build the app for "development", "testing" or "production".

    With no name, FLASK_CONFIG decides, falling back to development.
    """
    config_name = config_name or os.environ.get("FLASK_CONFIG", "development")
    try:
        config_class = CONFIGS[config_name]
    except KeyError:
        raise ValueError(
            f"Unknown config {config_name!r}; expected one of {sorted(CONFIGS)}"
        ) from None

    # OpenAPI is a Flask subclass that also builds an OpenAPI spec from the
    # Pydantic models on each route and serves the docs under DOCS_PREFIX:
    # the spec at /api/docs/openapi.json, Swagger UI at /api/docs/.
    app = OpenAPI(
        __name__,
        info=Info(title="URL Shortener API", version="1.0.0"),
        doc_prefix=DOCS_PREFIX,
        # Invalid input is a 400 in our error shape, not the default 422
        validation_error_status=400,
        validation_error_callback=pydantic_error_response,
    )
    # The docs home is normally a page listing the installed UIs. We only
    # install Swagger, so show it there directly.
    app.view_functions["openapi.openapi"] = app.view_functions["swagger.swagger"]
    app.config.from_object(config_class)

    missing = [env for key, env in config_class.REQUIRED.items() if not app.config.get(key)]
    if missing:
        raise RuntimeError(
            f"Set these environment variables for {config_name}: {', '.join(missing)}"
        )

    # Behind a reverse proxy (Render, nginx), every request arrives from the
    # proxy's address. ProxyFix reads the real client IP and scheme from the
    # X-Forwarded-* headers that the last NUM_PROXIES proxies added. Headers
    # beyond those could be written by the client, so they're ignored.
    proxies = app.config["NUM_PROXIES"]
    if proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies)

    db.init_app(app)
    # Import the models so they register on db.metadata (Alembic reads it)
    from app import models  # noqa: F401

    migrate.init_app(app, db)
    limiter.init_app(app)

    from app.api import bp as api_bp

    app.register_api(api_bp)

    from app.web import bp as web_bp

    app.register_blueprint(web_bp)
    register_error_handlers(app)

    @app.after_request
    def security_headers(response):
        # nosniff: browsers must trust our Content-Type (JSON stays JSON,
        # never guessed to be HTML or script). DENY: no site may show our
        # pages, Swagger UI included, inside a frame (clickjacking).
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    return app
