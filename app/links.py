"""Creating and looking up short links. Routes call these; they don't touch HTTP."""

import secrets
import string
from datetime import timedelta

from flask import current_app
from sqlalchemy import ColumnElement, and_, func, or_, select, update
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


# Both judged by the database clock, the same clock that sets created_at,
# so the app server's clock can't disagree with it.
def _is_expired() -> ColumnElement[bool]:
    return and_(Link.expires_at.is_not(None), Link.expires_at <= func.now())


def _is_live() -> ColumnElement[bool]:
    return or_(Link.expires_at.is_(None), Link.expires_at > func.now())


def _new_expiry() -> ColumnElement | None:
    """expires_at for a link created now: now() + LINK_TTL_DAYS, or None."""
    days = current_app.config["LINK_TTL_DAYS"]
    return func.now() + timedelta(days=days) if days else None


def _find_random_link(url: str) -> tuple[Link, bool] | None:
    """The random link for url, if there is one, and whether it has expired."""
    query = select(Link, _is_expired()).where(Link.original_url == url, Link.is_custom.is_(False))
    row = db.session.execute(query).first()
    return None if row is None else (row[0], row[1])


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
    link = Link(code=code, original_url=url, is_custom=True, expires_at=_new_expiry())
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
    found = _find_random_link(url)
    if found is not None and not found[1]:
        return found[0], False
    # If the URL's random link has expired, we can't insert a second one: the
    # partial unique index allows one random row per URL. So the expired row
    # gets a new code, a new expiry and zero clicks instead (see _reissue).
    expired = found[0] if found is not None else None

    for attempt in range(1, MAX_CODE_ATTEMPTS + 1):
        code = generate_code()
        try:
            if expired is None:
                link = Link(code=code, original_url=url, is_custom=False, expires_at=_new_expiry())
                db.session.add(link)
                db.session.commit()
                return link, True
            link = _reissue(expired, code)
            if link is not None:
                return link, True
            # Another request reissued it first. Its link is fresh, so use it.
            return _existing_random_link(url)
        except IntegrityError as exc:
            db.session.rollback()
            constraint = _broken_constraint(exc)
            if constraint == RANDOM_URL_INDEX:
                # Another request shortened the same URL between our SELECT
                # and our INSERT. Its link is committed now, so use it.
                return _existing_random_link(url)
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


def _reissue(link: Link, code: str) -> Link | None:
    """Turn an expired random link into a fresh one, in one statement.

    UPDATE links SET code = :code, expires_at = ..., clicks = 0, ...
    WHERE id = :id AND <still expired> RETURNING id

    If two requests try this at once, the second waits for the first's row
    lock, then sees the row is no longer expired and updates nothing. It
    returns None in that case.
    """
    statement = (
        update(Link)
        .where(Link.id == link.id, _is_expired())
        .values(
            code=code,
            expires_at=_new_expiry(),
            clicks=0,
            created_at=func.now(),
            last_clicked_at=None,
        )
        .returning(Link.id)
        .execution_options(synchronize_session=False)
    )
    link_id = db.session.scalar(statement)
    if link_id is None:
        db.session.rollback()
        return None
    db.session.commit()
    # The commit marked the session's copy as stale, so this reloads it
    return db.session.get(Link, link_id)


def _existing_random_link(url: str) -> tuple[Link, bool]:
    found = _find_random_link(url)
    if found is None or found[1]:
        raise RuntimeError(f"Expected a live random link for {url!r}")
    return found[0], False


def get_link(code: str) -> Link:
    link = db.session.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise not_found(code)
    return link


def record_click(code: str) -> str:
    """Count one click and return the URL to redirect to, in one statement.

    UPDATE links SET clicks = clicks + 1, last_clicked_at = now()
    WHERE code = :code AND <not expired> RETURNING original_url

    The database adds 1 to whatever value it holds at that moment, under a
    row lock, so simultaneous clicks can't overwrite each other. Reading
    clicks into Python, adding 1 and saving would lose some of them.
    An expired link matches nothing, so its clicks stop counting.
    """
    statement = (
        update(Link)
        .where(Link.code == code, _is_live())
        .values(clicks=Link.clicks + 1, last_clicked_at=func.now())
        .returning(Link.original_url)
        # Don't also refresh Link objects held in this session (that would
        # cost a second query); nothing here holds any.
        .execution_options(synchronize_session=False)
    )
    url = db.session.scalar(statement)
    if url is None:
        db.session.rollback()
        # Nothing updated: either no such code, or it has expired. Only this
        # failure path pays for the second query.
        expires_at = db.session.scalar(select(Link.expires_at).where(Link.code == code))
        if expires_at is not None:
            raise ApiError(410, "link_expired", f"The short link '{code}' has expired")
        raise not_found(code)
    db.session.commit()
    return url


def not_found(code: str) -> ApiError:
    return ApiError(404, "not_found", f"No short link with the code '{code}'")
