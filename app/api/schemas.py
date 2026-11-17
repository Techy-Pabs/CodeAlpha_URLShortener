"""Pydantic models for request and response bodies."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.urls import check_custom_code, normalize_url


class CreateLinkRequest(BaseModel):
    # Unknown fields (say, a typo like "customCode") are an error, not ignored
    model_config = ConfigDict(extra="forbid")

    url: str = Field(description="The http(s) URL to shorten", examples=["https://example.com/a"])
    custom_code: str | None = Field(
        default=None,
        description="Optional code of your own: 3-30 letters, digits, - or _",
        examples=["my-link"],
    )

    # PydanticCustomError keeps our message as is; a plain ValueError would
    # come out as "Value error, URL must ..."
    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        try:
            return normalize_url(value)
        except ValueError as exc:
            raise PydanticCustomError("invalid_url", str(exc)) from None

    @field_validator("custom_code")
    @classmethod
    def _check_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return check_custom_code(value)
        except ValueError as exc:
            raise PydanticCustomError("invalid_code", str(exc)) from None


class LinkResponse(BaseModel):
    code: str
    short_url: str
    original_url: str
    clicks: int
    created_at: datetime


class LinkStatsResponse(LinkResponse):
    last_clicked_at: datetime | None
    is_custom: bool


class CodePath(BaseModel):
    code: str = Field(description="The short code, e.g. aB3xK9q")


class HealthResponse(BaseModel):
    status: str
