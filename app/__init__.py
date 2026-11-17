"""App factory: create_app builds a configured Flask app."""

import os

from flask import Flask

from app.config import CONFIGS
from app.errors import register_error_handlers
from app.extensions import db, migrate


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

    app = Flask(__name__)
    app.config.from_object(config_class)

    missing = [env for key, env in config_class.REQUIRED.items() if not app.config.get(key)]
    if missing:
        raise RuntimeError(
            f"Set these environment variables for {config_name}: {', '.join(missing)}"
        )

    db.init_app(app)
    # Import the models so they register on db.metadata (Alembic reads it)
    from app import models  # noqa: F401

    migrate.init_app(app, db)

    from app.api import bp as api_bp

    app.register_blueprint(api_bp)
    register_error_handlers(app)

    return app
