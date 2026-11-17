from datetime import datetime

import pytest
from psycopg import errors
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Link


def test_link_defaults():
    link = Link(code="abc123", original_url="https://example.com/a/long/path")
    db.session.add(link)
    db.session.commit()
    db.session.refresh(link)

    assert link.id is not None
    assert link.clicks == 0
    assert isinstance(link.created_at, datetime)
    assert link.created_at.tzinfo is not None
    assert link.last_clicked_at is None


def test_clicks_default_applies_to_raw_sql_inserts():
    # server_default: the database fills clicks in even without the ORM
    db.session.execute(
        text("INSERT INTO links (code, original_url) VALUES ('raw1', 'https://example.com')")
    )
    db.session.commit()

    assert db.session.scalar(text("SELECT clicks FROM links WHERE code = 'raw1'")) == 0


def test_duplicate_code_is_rejected():
    db.session.add(Link(code="dup", original_url="https://example.com/1"))
    db.session.commit()

    db.session.add(Link(code="dup", original_url="https://example.com/2"))
    with pytest.raises(IntegrityError) as exc_info:
        db.session.commit()

    assert isinstance(exc_info.value.orig, errors.UniqueViolation)
    assert exc_info.value.orig.diag.constraint_name == "ix_links_code"


def test_negative_clicks_are_rejected():
    db.session.add(Link(code="neg", original_url="https://example.com", clicks=-1))
    with pytest.raises(IntegrityError) as exc_info:
        db.session.commit()

    assert isinstance(exc_info.value.orig, errors.CheckViolation)
    assert exc_info.value.orig.diag.constraint_name == "ck_links_clicks_non_negative"
