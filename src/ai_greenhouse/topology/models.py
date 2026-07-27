"""ORM models of the topology module."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.core.types import (
    DEFAULT_TIMEZONE,
    MAX_CODE_LENGTH,
    MAX_NAME_LENGTH,
    MAX_TIMEZONE_LENGTH,
)
from ai_greenhouse.infrastructure.database.base import (
    Base,
    StatusMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Site(UUIDPrimaryKeyMixin, TimestampMixin, StatusMixin, Base):
    """A physical location and the root of the topology.

    A site is never deleted; ``status`` moves to ``archived`` instead. The
    ``code`` is unique across the whole installation because Milestone 1 has no
    organization above the site to scope it to.

    Attributes:
        name: Human-readable label, 1-200 characters after stripping.
        code: Stable slug, unique across all sites and immutable after creation.
        timezone: IANA timezone name the site's local time is expressed in.
    """

    __tablename__ = "sites"

    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    code: Mapped[str] = mapped_column(
        String(MAX_CODE_LENGTH),
        nullable=False,
        unique=True,
        index=True,
    )
    timezone: Mapped[str] = mapped_column(
        String(MAX_TIMEZONE_LENGTH),
        nullable=False,
        default=DEFAULT_TIMEZONE,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"Site(id={self.id!r}, code={self.code!r})"
