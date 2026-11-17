"""One JSON shape for every error the app returns.

    {"error": {"code": "not_found", "message": "...", "details": [...]}}

"details" appears only on validation errors and lists each bad field.
"""

from flask import Flask, Response, current_app, jsonify
from pydantic import BaseModel, ValidationError
from werkzeug.exceptions import HTTPException

from app.extensions import db

BODY_NOT_AN_OBJECT = 'Request body must be a JSON object, e.g. {"url": "..."}'


# Models of the shape, so the Swagger docs can show it
class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo


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
    details = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err["loc"])
        # An empty location means the body as a whole was wrong: missing,
        # not JSON, or JSON that isn't an object
        message = err["msg"] if field else BODY_NOT_AN_OBJECT
        details.append({"field": field, "message": message})
    return validation_error(details)


def pydantic_error_response(exc: ValidationError) -> Response:
    """flask-openapi3 calls this when a request body or path fails validation."""
    error = from_pydantic(exc)
    response = jsonify(error_body(error.code, error.message, error.details))
    response.status_code = error.status
    return response


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(exc: ApiError):
        return error_body(exc.code, exc.message, exc.details), exc.status

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        # Some exceptions arrive with a finished response, such as the
        # validation errors above. Send those as they are.
        if exc.response is not None:
            return exc.response
        # Flask's own 404, 405, 429 and so on, as JSON instead of HTML.
        # "Method Not Allowed" becomes "method_not_allowed".
        code = (exc.name or "error").lower().replace(" ", "_")
        response = jsonify(error_body(code, exc.description or exc.name))
        response.status_code = exc.code or 500
        # Keep headers the exception carries, such as Allow on a 405
        for name, value in exc.get_headers():
            if name.lower() != "content-type":
                response.headers[name] = value
        return response

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        # Anything not handled above is a bug: log the traceback, hide it
        # from the client.
        db.session.rollback()
        current_app.logger.exception("Unhandled error")
        return error_body("internal_server_error", "Something went wrong on our side."), 500
