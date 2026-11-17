import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db, limiter


def _create_database_if_missing(url: str) -> None:
    """Create the test database on the same Postgres server if it isn't there yet."""
    target = make_url(url)
    # Connect to the built-in "postgres" database to run CREATE DATABASE
    engine = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                name = engine.dialect.identifier_preparer.quote(target.database)
                conn.execute(text(f"CREATE DATABASE {name}"))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def app():
    app = create_app("testing")
    url = app.config["SQLALCHEMY_DATABASE_URI"]
    # drop_all() below deletes every table, so never point it at real data
    if not make_url(url).database.endswith("_test"):
        pytest.exit("TEST_DATABASE_URL must name a database ending in _test", returncode=1)
    _create_database_if_missing(url)

    with app.app_context():
        db.drop_all()  # leftovers from a run that crashed part way
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _empty_tables(app):
    """Give every test empty tables."""
    yield
    db.session.rollback()
    for table in reversed(db.metadata.sorted_tables):
        db.session.execute(table.delete())
    db.session.commit()


@pytest.fixture(autouse=True)
def _reset_rate_limits(app):
    """Start every test with fresh rate limit counters."""
    limiter.reset()
    yield


@pytest.fixture
def client(app):
    return app.test_client()
