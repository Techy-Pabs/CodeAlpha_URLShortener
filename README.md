# CodeAlpha URL Shortener

[![CI](https://github.com/Techy-Pabs/CodeAlpha_URLShortener/actions/workflows/ci.yml/badge.svg)](https://github.com/Techy-Pabs/CodeAlpha_URLShortener/actions/workflows/ci.yml)
[![Live demo](https://img.shields.io/badge/live%20demo-onrender.com-46e3b7)](https://codealpha-urlshortener-0zcl.onrender.com)

**Live demo:** https://codealpha-urlshortener-0zcl.onrender.com (opens the
Swagger UI at
[/api/docs/](https://codealpha-urlshortener-0zcl.onrender.com/api/docs/)). It
runs on Render's free plan, which sleeps when idle, so the first load can take
up to a minute. Links made on the demo expire after 7 days.

A REST API that turns long URLs into short links, redirects visitors who
open a short link, and counts the clicks. It's written in Flask with a
PostgreSQL database. I built it for the CodeAlpha Backend Development
internship, Task 1.

## Features

- Shorten any `http` or `https` URL to a random 7-character code, or pick
  your own code (3 to 30 letters, digits, `-` or `_`).
- Shortening the same URL twice returns the existing link instead of a new
  one. Custom codes are always separate links.
- `GET /<code>` redirects to the original URL with a 302 and counts the
  click. Clicks that arrive at the same moment are all counted.
- A stats endpoint shows the click count, when the link was created, when it
  was last clicked and when it expires.
- Optional link expiry (`LINK_TTL_DAYS`). Expired links answer 410 Gone.
- Input checks with clear messages: the URL must be `http(s)` with a host,
  can't point back at the shortener itself, and unknown fields are rejected.
- Every error, including 404, 405 and 429, comes back as JSON in one shape.
- Rate limit on link creation: 10 per minute per IP address.
- Swagger UI at `/api/docs/`, generated from the same Pydantic models that
  validate requests.
- Health check at `/api/health` that also checks the database.
- Docker image that runs as a non-root user, waits for the database and
  applies migrations before it starts.

## Tech stack

- Python 3.14, Flask 3.1
- PostgreSQL 16, SQLAlchemy 2.1, Flask-SQLAlchemy, Flask-Migrate (Alembic)
- psycopg 3 as the database driver
- flask-openapi3 and Pydantic 2 for request validation and the OpenAPI docs
- Flask-Limiter for rate limiting
- gunicorn in the Docker image
- pytest and Ruff, run by GitHub Actions on every push

## How it works

### Short codes

A random code is 7 characters from the 62 letters and digits, picked with
Python's `secrets` module, so a code can't be predicted from the ones
before it. That gives about 3.5 trillion possible codes.

The app doesn't check whether a code is free before using it. It inserts the
row and lets the unique index on `code` reject a duplicate. A check followed
by an insert can be beaten by another request in between; the index can't.
If a random code is already taken, the app tries a new one, up to 5 times,
and then answers 500 (with that many codes, a single collision is already
very unlikely). A taken custom code answers 409 straight away.

Codes are case-sensitive, so `/AbC123x` and `/abc123x` are different links.
A few codes are reserved because the app uses those paths itself: `api`,
`docs`, `health`, `static` and `admin`.

### Duplicate URLs and the partial unique index

URLs are cleaned before they are stored: spaces around them are trimmed and
the scheme and host are lowercased. The path, query and fragment are kept as
sent, because many servers treat `/Page` and `/page` as different pages.

The rule "one random code per URL" lives in the database as a partial
unique index:

```sql
CREATE UNIQUE INDEX uq_links_original_url_random ON links (original_url)
WHERE NOT is_custom;
```

Only rows with a random code are in the index, so custom codes for the same
URL are allowed, as many as you like. When a request comes in without a
custom code, the app first looks for an existing random link and returns it
with 200. If two requests for the same new URL arrive together, both may
find nothing, but only one insert can succeed. The other gets a unique
violation on this index, and the app answers it with the link that won. Both
clients end up with the same code.

### Why the redirect is a 302

A 301 means "moved permanently", and browsers cache it. The next time
someone opens the short link, the browser goes straight to the long URL
without asking the shortener, so the click is never counted. An expired link
would also keep working from the cache. A 302 is not cached by default, so
every visit reaches the app.

### Counting clicks safely

Each click is one SQL statement:

```sql
UPDATE links SET clicks = clicks + 1, last_clicked_at = now()
WHERE code = :code AND (expires_at IS NULL OR expires_at > now())
RETURNING original_url;
```

The database adds 1 to whatever value it holds at that moment, under a row
lock, and returns the URL to redirect to. The obvious version reads `clicks`
into Python, adds 1 and saves it. When two clicks arrive together, both read
the same number, both save that number plus one, and one click is lost.

`tests/test_redirect_and_stats.py` has a test for this. It starts 20
threads, holds them at a barrier, releases them all at once to click the
same link, and then checks that the count is exactly 20. I swapped in the
read-add-save version to make sure the test can catch it: the test failed
on every run, with only 2 of the 20 clicks counted.

### Link expiry

Expiry is off by default. Set `LINK_TTL_DAYS` (a whole number, at least 1)
and every new link, random or custom, gets an `expires_at` that many days
after it was created. The public demo uses 7 days, so links people create
there don't stay up forever.

- **Redirects.** Once `expires_at` has passed, `GET /<code>` answers
  `410 Gone` with the usual JSON error (`link_expired`). The click isn't
  counted: the `UPDATE` above has the expiry in its `WHERE`, so it matches
  nothing.
- **Stats** keep working for an expired link and include `expires_at`.
- **The same URL again.** If someone shortens a URL whose random link has
  expired, they get a new link (201) instead of the dead one. The partial
  unique index allows only one random row per URL, so a second row can't be
  inserted. Instead the app reuses the expired row: in one `UPDATE` it gives
  it a new code, a new expiry, zero clicks and a new `created_at`. The old
  code then answers 404, as if it never existed. That `UPDATE` only matches
  while the row is still expired, so if two requests try this at the same
  time, the second one waits for the first's row lock, finds the row is no
  longer expired, changes nothing and returns the first one's new link.
  There's a test that runs 8 such requests at once and checks they all get
  the same code.
- **Custom codes** are not reissued. An expired custom code keeps answering
  410 and stays taken.
- Expiry times are compared using the database clock, the same clock that
  sets `created_at`.

## API endpoints

| Method | Path | What it does | Success |
| --- | --- | --- | --- |
| `POST` | `/api/links` | Shorten a URL. Body: `{"url": "...", "custom_code": "..."}`, `custom_code` optional | 201 new link, 200 existing link |
| `GET` | `/api/links/{code}` | A link's stats. Doesn't count as a click | 200 |
| `GET` | `/{code}` | Redirect to the original URL and count the click | 302 |
| `GET` | `/api/health` | `{"status": "ok"}` if the database answers, otherwise 503 | 200 |
| `GET` | `/api/docs/` | Swagger UI | 200 |
| `GET` | `/api/docs/openapi.json` | The OpenAPI spec | 200 |
| `GET` | `/` | Redirects to the Swagger UI | 302 |

A created link looks like this:

```json
{
  "code": "I7SZV8q",
  "short_url": "http://localhost:8002/I7SZV8q",
  "original_url": "https://docs.python.org/3/library/secrets.html",
  "clicks": 0,
  "created_at": "2026-11-17T21:02:22.273297Z",
  "expires_at": "2026-11-24T21:02:22.273297Z"
}
```

`expires_at` is `null` when expiry is off. The stats endpoint adds
`last_clicked_at` and `is_custom`.

## The error format

Every error, from the app or from Flask itself, has the same shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "url: URL must start with http:// or https://",
    "details": [
      {"field": "url", "message": "URL must start with http:// or https://"}
    ]
  }
}
```

`details` only appears on validation errors, with one entry per bad field,
so a client can show each message next to its field.

| Status | `code` | When |
| --- | --- | --- |
| 400 | `validation_error` | Bad URL or custom code, unknown field, or a body that isn't a JSON object |
| 404 | `not_found` | No link with that code, or an unknown path |
| 405 | `method_not_allowed` | Wrong HTTP method for the path |
| 409 | `code_taken` | The custom code is already in use |
| 410 | `link_expired` | The link has expired |
| 429 | `too_many_requests` | More than 10 links created in a minute from one IP |
| 500 | `code_generation_failed` | 5 random codes in a row were taken |
| 500 | `internal_server_error` | A bug. The details go to the log, not to the client |

## Running with Docker

You need Docker with Compose.

```bash
git clone https://github.com/Techy-Pabs/CodeAlpha_URLShortener.git
cd CodeAlpha_URLShortener
cp .env.example .env
# Edit .env: set SECRET_KEY and POSTGRES_PASSWORD (and the same password
# inside DATABASE_URL and TEST_DATABASE_URL).
docker compose up --build
```

Then open http://localhost:8002/api/docs/.

This starts two containers. `db` is PostgreSQL 16, reachable from your
machine on port 5435. `web` is the production image: it waits for the
database, applies the migrations and starts gunicorn, and is published on
port 8002. Compose points `DATABASE_URL` at the `db` container and sets
`FLASK_CONFIG=production`, whatever `.env` says.

To try link expiry, add `LINK_TTL_DAYS=7` to `.env` and run
`docker compose up -d` again.

## Running without Docker

You need Python 3.14 and a PostgreSQL server. The easiest way to get the
server is still Compose, with only the database:

```bash
cp .env.example .env            # then edit it as above
docker compose up -d db
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
flask db upgrade
flask run
```

`.env` sets `FLASK_APP=app` and `FLASK_RUN_PORT=8002`, so `flask run` serves
the development server on http://localhost:8002. It uses the same port as
the `web` container, so stop that first (`docker compose stop web`).

## Environment variables

| Variable | Required | Default | What it's for |
| --- | --- | --- | --- |
| `SECRET_KEY` | Yes | none | Flask's secret key. The app refuses to start without it |
| `DATABASE_URL` | Yes | none | PostgreSQL connection string. `postgres://` and `postgresql://` URLs are fine; the app switches them to the psycopg 3 driver |
| `BASE_URL` | Yes in production | `http://localhost:8002` in development | Public address that short links are built from. Links to this host are rejected |
| `FLASK_CONFIG` | No | `development` | `development`, `testing` or `production`. The Docker image sets `production` |
| `LINK_TTL_DAYS` | No | empty (never expire) | Days before a new link expires |
| `NUM_PROXIES` | No | `0` | How many reverse proxies are in front of the app. See the production notes |
| `PORT` | No | `8000` | Port gunicorn listens on inside the container. Render sets it |
| `WEB_CONCURRENCY` | No | `1` | Number of gunicorn workers |
| `FLASK_APP`, `FLASK_RUN_PORT` | Local only | | Let `flask run` and `flask db` find the app and use port 8002 |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Local only | | Set up the Compose database |
| `TEST_DATABASE_URL` | For tests | | A separate database whose name ends in `_test`. The tests create it if needed and drop its tables |

The app reads `.env` itself when it starts. Variables that are already set
in the environment win over the file.

## Tests and linting

```bash
pytest                  # 170 tests, needs the database from .env
ruff check .
ruff format --check .
```

The tests run against a real PostgreSQL database (`TEST_DATABASE_URL`), not
SQLite, because the partial unique index, the row locking and the
constraint names they check are Postgres behaviour. They cover link
creation and validation, the duplicate and race handling, redirects and the
20-thread click test, expiry, the error format, rate limiting, the Docker
entrypoint, the gunicorn settings and the production checks.

GitHub Actions runs on every push and pull request (see the badge at the
top). It starts Postgres 16, runs Ruff, applies the migrations, checks that
the migrations match the models (`flask db check`) and runs the tests.

## Secret check

A pre-commit hook in `.githooks/pre-commit` blocks a commit that stages a
`.env` file or a key file, a line that looks like a hardcoded `SECRET_KEY`,
a private key or a common API token, or any value copied from your local
`.env`. If the check itself errors, the commit is blocked too.

Git doesn't turn on repository hooks by itself, so after cloning run this
once:

```bash
git config core.hooksPath .githooks
```

## Deployment

The demo runs on [Render](https://render.com) as a free Docker web service,
with the database on [Neon](https://neon.tech) (free PostgreSQL). Docker
Compose is only for local use; on Render the container connects to Neon
over the internet.

### What the container does when it starts

`docker/entrypoint.sh` runs these steps in order. If one fails, it prints a
line starting with `ERROR:` and exits without starting the app. Render then
marks the deploy as failed and keeps the previous version running, so the
app never serves requests against a half-migrated database.

1. Waits for the database to answer (30 attempts). A Neon database that
   has been idle needs a moment to wake up.
2. Applies the migrations with `flask db upgrade`. This is also the first
   point where the app loads its settings, so a missing `SECRET_KEY`,
   `DATABASE_URL` or `BASE_URL` stops the deploy here, with the names of
   the missing variables in the log.
3. Starts gunicorn on `0.0.0.0:$PORT` with `WEB_CONCURRENCY` workers
   (1 by default).

### 1. Create the database on Neon

1. Create a Neon project. Choose PostgreSQL 16 to match the local setup,
   and a region close to the Render region you'll pick.
2. On the project dashboard, open **Connect**. Turn off **Connection
   pooling** to get the direct connection string (its host name doesn't
   contain `-pooler`). The app keeps its own small pool of connections, so
   it doesn't need Neon's pooler, and migrations run over this connection
   on every start.
3. Copy the connection string. It looks like this:

   ```
   postgresql://USER:PASSWORD@ep-example-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```

   Paste it into Render unchanged. The app switches `postgresql://` to the
   psycopg 3 driver and passes `sslmode=require` and
   `channel_binding=require` on to it, so the connection is encrypted.
   Treat the whole string as a secret, because it contains the database
   password.

The tables are created by the migrations on the first start.

### 2. Create the web service on Render

1. In Render, choose **New**, then **Web Service**, and connect this GitHub
   repository. Pick the `main` branch.
2. Set **Language** to **Docker**. Render builds the `Dockerfile` in the
   project root. Leave the Docker command empty, so the image's own
   entrypoint and gunicorn command run.
3. Choose the **Free** instance type, in a region close to your Neon
   region.
4. Under **Advanced**, set **Health Check Path** to `/api/health`. It
   returns 200 only when the database answers.
5. Add the environment variables below, then create the service.

Render gives the service an address such as `your-app.onrender.com` and
serves it over HTTPS. `BASE_URL` must be that address, because the short
links are built from it. If you don't know the address yet, create the
service with a placeholder `BASE_URL`, then correct it and let it redeploy.

Every push to `main` then triggers a new build and deploy.

### Environment variables on Render

| Variable | Value | What it's for |
| --- | --- | --- |
| `SECRET_KEY` | A new random value, not your local one | Flask's secret key. The app won't start without it |
| `DATABASE_URL` | The Neon direct connection string, unchanged | How the app reaches the database |
| `BASE_URL` | `https://your-app.onrender.com` | The address short links are built from. Links to this host are rejected |
| `LINK_TTL_DAYS` | `7` | Demo links expire after a week, so the public demo can't be used to host links forever |
| `NUM_PROXIES` | `1` | Lets the rate limit see each visitor's IP instead of Render's proxy. See the production notes |
| `WEB_CONCURRENCY` | Optional, `1` | Number of gunicorn workers. Keep it at 1 so the rate limit has a single count |

Already handled, so don't set them: `PORT` (Render sets it), `FLASK_CONFIG`
and `FLASK_APP` (the image sets `production` and `app`). `FLASK_RUN_PORT`,
`POSTGRES_*` and `TEST_DATABASE_URL` are only for local use.

Generate a secret key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### Free plan behaviour

- **It sleeps.** A free Render web service goes to sleep after about 15
  minutes without requests. The next request wakes it up, which can take up
  to about a minute (the container also checks the migrations again), so
  the first request after a quiet period is slow. It's quick again once
  awake.
- **Neon pauses too.** Neon's free plan suspends the database when it's
  idle and resumes it on the next query, which adds a short delay. Pooled
  connections that Neon closed in the meantime are tested before use
  (`pool_pre_ping`) and replaced, so they don't cause errors.
- **One instance, little memory.** 512 MB of RAM. One gunicorn worker fits
  easily. The rate limit counters live in that worker's memory and reset
  whenever the service restarts or wakes up.

## Production notes

- **Startup checks.** The app refuses to start if `SECRET_KEY`,
  `DATABASE_URL` or `BASE_URL` is missing, and names every missing one.
  The Docker entrypoint stops with an `ERROR:` line, without starting
  gunicorn, if the database can't be reached or a migration fails.
- **Security headers.** Every response carries
  `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY`.
- **The rate limit is per worker.** Flask-Limiter keeps its counters in
  memory. Each gunicorn worker has its own memory, so with `N` workers a
  client could create up to `N` times 10 links a minute, and the counts
  reset when the app restarts. That's why the image runs one worker by
  default. For strict limits with more workers, point
  `RATELIMIT_STORAGE_URI` at Redis.
- **Set `NUM_PROXIES` behind a proxy.** Behind a reverse proxy every
  request seems to come from the proxy's address, so all clients would
  share one rate limit. With `NUM_PROXIES=N`, Werkzeug's `ProxyFix` takes
  the client IP from the Nth entry from the end of `X-Forwarded-For` (and
  the scheme from `X-Forwarded-Proto`). Proxies append to that header, but
  a client can write anything at its start, so never set it higher than the
  number of proxies really there. Otherwise clients can pick their own IP
  and get around the limit.
- **Database connections.** `pool_pre_ping` tests each pooled connection
  before it's used and replaces it if the server closed it, which hosted
  databases such as Neon do with idle connections. Connection attempts time
  out after 5 seconds, so the health check answers 503 instead of hanging.

## Screenshots

All taken from the Swagger UI of the live demo, which runs with `LINK_TTL_DAYS=7`.

![Swagger UI listing the health, create link and stats endpoints](docs/screenshots/01-swagger-overview.png)

*The Swagger UI at `/api/docs/`.*

![POST /api/links returning 201 with a new random code](docs/screenshots/02-create-201.png)

*Shortening a new URL returns 201 with a random code and an expiry 7 days out.*

![The same POST again returning 200 with the same code](docs/screenshots/03-same-url-200.png)

*Shortening the same URL again returns 200 and the same link.*

![POST /api/links with a taken custom code returning 409](docs/screenshots/04-code-taken-409.png)

*Asking for a custom code that's already taken returns 409.*

![POST /api/links with javascript:alert(1) returning 400 with details](docs/screenshots/05-invalid-url-400.png)

*A `javascript:` URL is rejected with 400 and a `details` entry for the field.*

![GET /api/links/{code} showing one click and last_clicked_at](docs/screenshots/06-stats-after-one-click.png)

*Stats after one visit to the short link: one click and the time it happened.*
