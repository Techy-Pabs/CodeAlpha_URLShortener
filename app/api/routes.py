from flask import current_app
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import bp
from app.extensions import db


@bp.get("/health")
def health():
    """Report whether the app can reach its database."""
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Health check: database unreachable")
        return {"status": "unavailable"}, 503
    return {"status": "ok"}
