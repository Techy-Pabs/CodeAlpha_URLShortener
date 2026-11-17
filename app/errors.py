"""One JSON shape for every error the app returns.

    {"error": {"code": "not_found", "message": "...", "details": [...]}}

"details" appears only on validation errors and lists each bad field.
"""

from flask import Flask, current_app, json
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from app.extensions import db


class ApiError(Exception):
    """Raise from anywhere in a request to send a JSON error response."""

    def __init__(
        self, status: int, code: str, message: str, details: list[dict] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def error_body(code: str, message: str, details: list[dict] | None = None) -> dict:
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"error": error}


def validation_error(details: list[dict]) -> ApiError:
    """A 400 whose message repeats each problem, e.g. "url: URL must include a host"."""
    message = "; ".join(
        f"{d['field']}: {d['message']}" if d["field"] else d["message"] for d in details
    )
    return ApiError(400, "validation_error", message, details)


def from_pydantic(exc: ValidationError) -> ApiError:
    details = [
        {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return validation_error(details)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(exc: ApiError):
        return error_body(exc.code, exc.message, exc.details), exc.status

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        # Flask's own 404, 405, 429 and so on. get_response() keeps headers
        # such as Allow (405) and Retry-After (429); only the HTML body is
        # swapped for JSON. "Method Not Allowed" becomes "method_not_allowed".
        response = exc.get_response()
        code = (exc.name or "error").lower().replace(" ", "_")
        response.data = json.dumps(error_body(code, exc.description or exc.name))
        response.content_type = "application/json"
        return response

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        # Anything not handled above is a bug: log the traceback, hide it
        # from the client.
        db.session.rollback()
        current_app.logger.exception("Unhandled error")
        return error_body("internal_server_error", "Something went wrong on our side."), 500
