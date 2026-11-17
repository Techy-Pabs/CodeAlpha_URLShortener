"""Extension objects, created without an app and bound to one in create_app."""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Gives every index and constraint a predictable name, so Alembic
# migrations can find them again later when they need to change.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


db = SQLAlchemy(model_class=Base)
# Batch mode exists for SQLite, which can't ALTER most things in place and
# has to copy the whole table instead. We only run Postgres, which can, so
# new migrations use plain op.add_column / op.create_index calls.
migrate = Migrate(render_as_batch=False)

# Limits are keyed on the client IP. Behind a proxy that's only the real
# client once ProxyFix has rewritten remote_addr (see create_app).
limiter = Limiter(key_func=get_remote_address)
