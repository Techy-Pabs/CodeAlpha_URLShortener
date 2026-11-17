from flask import current_app, request
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import bp
from app.api.schemas import CreateLinkRequest, LinkResponse, LinkStatsResponse
from app.errors import from_pydantic, validation_error
from app.extensions import db
from app.links import create_link, get_link, short_url
from app.models import Link


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


def link_response(link: Link) -> dict:
    return LinkResponse(
        code=link.code,
        short_url=short_url(link.code),
        original_url=link.original_url,
        clicks=link.clicks,
        created_at=link.created_at,
    ).model_dump(mode="json")


@bp.post("/links")
def create():
    """Shorten a URL: 201 with a new link, or 200 with the existing one."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise validation_error(
            [{"field": "", "message": 'Request body must be a JSON object, e.g. {"url": "..."}'}]
        )
    try:
        body = CreateLinkRequest.model_validate(payload)
    except ValidationError as exc:
        raise from_pydantic(exc) from None

    link, created = create_link(body.url, body.custom_code)
    return link_response(link), 201 if created else 200


@bp.get("/links/<code>")
def stats(code: str):
    """Details and click count for one link. Viewing stats is not a click."""
    link = get_link(code)
    return LinkStatsResponse(
        code=link.code,
        short_url=short_url(link.code),
        original_url=link.original_url,
        clicks=link.clicks,
        created_at=link.created_at,
        last_clicked_at=link.last_clicked_at,
        is_custom=link.is_custom,
    ).model_dump(mode="json")
