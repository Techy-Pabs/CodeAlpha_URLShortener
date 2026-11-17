"""Creating and looking up short links. Routes call these; they don't touch HTTP."""

import secrets
import string

from flask import current_app
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.errors import ApiError, validation_error
from app.extensions import db
from app.models import CODE_INDEX, RANDOM_URL_INDEX, Link
from app.urls import url_host

BASE62 = string.digits + string.ascii_letters
RANDOM_CODE_LENGTH = 7  # 62**7 is about 3.5 trillion codes
MAX_CODE_ATTEMPTS = 5


def generate_code() -> str:
    # secrets, not random: codes shouldn't be guessable from earlier ones
    return "".join(secrets.choice(BASE62) for _ in range(RANDOM_CODE_LENGTH))


def short_url(code: str) -> str:
    return f"{current_app.config['BASE_URL'].rstrip('/')}/{code}"


def _broken_constraint(exc: IntegrityError) -> str | None:
    """Name of the unique index or constraint an insert violated, if any."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def _find_random_link(url: str) -> Link | None:
    query = select(Link).where(Link.original_url == url, Link.is_custom.is_(False))
    return db.session.scalar(query)


def _reject_own_host(url: str) -> None:
    """A short link to ourselves would redirect to ourselves, possibly forever."""
    own_host = url_host(current_app.config["BASE_URL"])
    if own_host and url_host(url) == own_host:
        raise validation_error(
            [
                {
                    "field": "url",
                    "message": f"URL must not point at this shortener ({own_host})",
                }
            ]
        )


def create_link(url: str, custom_code: str | None) -> tuple[Link, bool]:
    """Return (link, created). url must already be normalized.

    created is False when an existing random link for the URL was reused.
    """
    _reject_own_host(url)
    if custom_code is not None:
        return _create_custom(url, custom_code), True
    return _create_random(url)


def _create_custom(url: str, code: str) -> Link:
    link = Link(code=code, original_url=url, is_custom=True)
    db.session.add(link)
    try:
        # No "is it taken?" SELECT first: the unique index answers that,
        # and unlike a SELECT it can't be beaten by a request in between.
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        if _broken_constraint(exc) == CODE_INDEX:
            raise ApiError(409, "code_taken", f"The code '{code}' is already taken") from None
        raise
    return link


def _create_random(url: str) -> tuple[Link, bool]:
    existing = _find_random_link(url)
    if existing is not None:
        return existing, False

    for attempt in range(1, MAX_CODE_ATTEMPTS + 1):
        link = Link(code=generate_code(), original_url=url, is_custom=False)
        db.session.add(link)
        try:
            db.session.commit()
            return link, True
        except IntegrityError as exc:
            db.session.rollback()
            constraint = _broken_constraint(exc)
            if constraint == RANDOM_URL_INDEX:
                # Another request shortened the same URL between our SELECT
                # and our INSERT. Its link is committed now, so use it.
                existing = _find_random_link(url)
                if existing is not None:
                    return existing, False
                raise
            if constraint != CODE_INDEX:
                raise
            current_app.logger.warning(
                "Random code collision (attempt %d of %d)", attempt, MAX_CODE_ATTEMPTS
            )

    current_app.logger.error(
        "Could not generate a unique short code after %d attempts; giving up", MAX_CODE_ATTEMPTS
    )
    raise ApiError(
        500, "code_generation_failed", "Could not generate a unique short code. Please try again."
    )


def get_link(code: str) -> Link:
    link = db.session.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise not_found(code)
    return link


def record_click(code: str) -> str:
    """Count one click and return the URL to redirect to, in one statement.

    UPDATE links SET clicks = clicks + 1, last_clicked_at = now()
    WHERE code = :code RETURNING original_url

    The database adds 1 to whatever value it holds at that moment, under a
    row lock, so simultaneous clicks can't overwrite each other. Reading
    clicks into Python, adding 1 and saving would lose some of them.
    """
    statement = (
        update(Link)
        .where(Link.code == code)
        .values(clicks=Link.clicks + 1, last_clicked_at=func.now())
        .returning(Link.original_url)
        # Don't also refresh Link objects held in this session (that would
        # cost a second query); nothing here holds any.
        .execution_options(synchronize_session=False)
    )
    url = db.session.scalar(statement)
    if url is None:
        db.session.rollback()
        raise not_found(code)
    db.session.commit()
    return url


def not_found(code: str) -> ApiError:
    return ApiError(404, "not_found", f"No short link with the code '{code}'")
