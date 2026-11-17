from flask import Blueprint

bp = Blueprint("api", __name__, url_prefix="/api")

from app.api import routes  # noqa: E402, F401  (registers the routes on bp)
