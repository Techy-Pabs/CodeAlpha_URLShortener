from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

CODE_MAX_LENGTH = 30
URL_MAX_LENGTH = 2048


class Link(db.Model):
    """A short code and the URL it redirects to."""

    __tablename__ = "links"
    __table_args__ = (CheckConstraint("clicks >= 0", name="clicks_non_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique + index gives one unique index, used for every redirect lookup
    code: Mapped[str] = mapped_column(String(CODE_MAX_LENGTH), unique=True, index=True)
    original_url: Mapped[str] = mapped_column(String(URL_MAX_LENGTH))
    # default fills it in Python; server_default fills it for raw SQL inserts too
    clicks: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Link {self.code}>"
