from flask_openapi3 import APIBlueprint

# Like a Flask Blueprint, but its routes also land in the OpenAPI docs
bp = APIBlueprint("api", __name__, url_prefix="/api")

from app.api import routes  # noqa: E402, F401  (registers the routes on bp)
