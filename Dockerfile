FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # The flask command (used for migrations) finds create_app in "app"
    FLASK_APP=app \
    FLASK_CONFIG=production

WORKDIR /app

# Unprivileged user that runs the app. The code below is copied as root and
# this user can't write to it, so a compromised process can't rewrite it.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

# Dependencies first, in their own layer: rebuilt only when
# requirements.txt changes, not on every code edit.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

USER app

EXPOSE 8000

# Port, worker count and logging come from gunicorn.conf.py, which reads
# PORT (set by Render, 8000 otherwise) and WEB_CONCURRENCY (default 1).
# "app:create_app()" tells gunicorn to call the factory to get the app.
# --no-control-socket: gunicorn 27's runtime control socket would live in the
# user's home directory, which this user doesn't have (and we don't use it).
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "app:create_app()", "--config", "gunicorn.conf.py", "--no-control-socket"]
