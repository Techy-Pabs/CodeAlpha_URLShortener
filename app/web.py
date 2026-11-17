"""Routes outside /api: the home page and the short links themselves.

A plain Blueprint, so these stay out of the API docs.
"""

from flask import Blueprint, redirect, url_for

from app.links import record_click

bp = Blueprint("web", __name__)


@bp.get("/")
def home():
    return redirect(url_for("openapi.openapi"))


@bp.get("/<code>")
def follow(code: str):
    # 302 (temporary), not 301 (permanent): browsers cache a 301 and skip us
    # on the next visit, so later clicks would never be counted.
    return redirect(record_click(code), code=302)
