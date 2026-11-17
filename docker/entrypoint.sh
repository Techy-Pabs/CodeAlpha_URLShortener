#!/bin/sh
# Wait for Postgres, apply migrations, then run the container's command
# (gunicorn by default, or e.g. "flask shell").
#
# Any failure stops the container with a non-zero exit code before the app
# starts, so it never serves requests against an out-of-date schema. On
# Render that marks the deploy as failed and keeps the previous version up.
set -e

fail() {
    echo "ERROR: $1" >&2
    echo "ERROR: Startup stopped; the app was not started." >&2
    exit 1
}

# A Neon database that has been idle can take a few seconds to wake up, and
# in docker compose the db container may still be starting.
python - <<'PY' || fail "Could not reach Postgres (see above)."
import os
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.config import database_uri

url = database_uri(os.environ.get("DATABASE_URL"))
if not url:
    sys.exit("DATABASE_URL is not set.")

engine = create_engine(url, connect_args={"connect_timeout": 5})
for attempt in range(1, 31):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Postgres is ready.", flush=True)
        break
    except OperationalError:
        print(f"Waiting for Postgres ({attempt}/30)...", flush=True)
        time.sleep(1)
else:
    sys.exit("Postgres was not reachable after 30 attempts.")
engine.dispose()
PY

flask db upgrade || fail "Database migrations failed (see above)."

exec "$@"
