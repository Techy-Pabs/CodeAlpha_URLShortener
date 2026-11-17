"""Routes outside /api: the short links themselves."""

from flask import Blueprint, redirect

from app.links import record_click

bp = Blueprint("web", __name__)


@bp.get("/<code>")
def follow(code: str):
    # 302 (temporary), not 301 (permanent): browsers cache a 301 and skip us
    # on the next visit, so later clicks would never be counted.
    return redirect(record_click(code), code=302)
