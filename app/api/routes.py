from flask import current_app
from flask_openapi3 import Tag
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import bp
from app.api.schemas import (
    CodePath,
    CreateLinkRequest,
    HealthResponse,
    LinkResponse,
    LinkStatsResponse,
)
from app.errors import ErrorResponse
from app.extensions import db, limiter
from app.links import create_link, get_link, short_url
from app.models import Link

links_tag = Tag(name="links", description="Create short links and read their stats")
system_tag = Tag(name="system")

CREATE_LIMIT = "10 per minute"


@bp.get("/health", tags=[system_tag], responses={200: HealthResponse, 503: HealthResponse})
def health():
    """Health check

    Reports whether the app can reach its database.
    """
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


@bp.post(
    "/links",
    tags=[links_tag],
    responses={
        201: LinkResponse,
        200: LinkResponse,
        400: ErrorResponse,
        409: ErrorResponse,
        429: ErrorResponse,
        500: ErrorResponse,
    },
)
@limiter.limit(
    CREATE_LIMIT,
    error_message=f"Too many links created: the limit is {CREATE_LIMIT} per IP address. "
    "Try again shortly.",
)
def create(body: CreateLinkRequest):
    """Shorten a URL

    Returns 201 with a new link. Without custom_code, a URL that already has a
    random code returns that link with 200 instead. Limited to 10 per minute
    per IP address.
    """
    link, created = create_link(body.url, body.custom_code)
    return link_response(link), 201 if created else 200


@bp.get(
    "/links/<code>",
    tags=[links_tag],
    responses={200: LinkStatsResponse, 404: ErrorResponse},
)
def stats(path: CodePath):
    """Get a link's stats

    Details and click count for one link. Viewing stats is not a click.
    """
    link = get_link(path.code)
    return LinkStatsResponse(
        code=link.code,
        short_url=short_url(link.code),
        original_url=link.original_url,
        clicks=link.clicks,
        created_at=link.created_at,
        last_clicked_at=link.last_clicked_at,
        is_custom=link.is_custom,
    ).model_dump(mode="json")
