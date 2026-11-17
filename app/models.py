from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

CODE_MAX_LENGTH = 30
URL_MAX_LENGTH = 2048

# Index names, also used to tell apart which unique rule an insert broke
CODE_INDEX = "ix_links_code"
RANDOM_URL_INDEX = "uq_links_original_url_random"


class Link(db.Model):
    """A short code and the URL it redirects to."""

    __tablename__ = "links"
    __table_args__ = (
        CheckConstraint("clicks >= 0", name="clicks_non_negative"),
        # At most one *random* code per URL, so shortening the same URL twice
        # returns the first link. Custom codes are left out (WHERE NOT
        # is_custom), so anyone can still pick their own code for that URL.
        Index(
            RANDOM_URL_INDEX,
            "original_url",
            unique=True,
            postgresql_where=text("NOT is_custom"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique + index gives one unique index, used for every redirect lookup
    code: Mapped[str] = mapped_column(String(CODE_MAX_LENGTH), unique=True, index=True)
    original_url: Mapped[str] = mapped_column(String(URL_MAX_LENGTH))
    # True when the user chose the code, False when we generated it
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # default fills it in Python; server_default fills it for raw SQL inserts too
    clicks: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # After this moment the link answers 410 Gone. NULL: it never expires.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Link {self.code}>"
