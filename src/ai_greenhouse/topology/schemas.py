"""Request and response schemas of the topology module."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ai_greenhouse.core.types import DEFAULT_TIMEZONE, CodeStr, NameStr, TimezoneStr
from ai_greenhouse.infrastructure.database.base import StatusEnum


class SiteCreate(BaseModel):
    """Body accepted by ``POST /api/v1/sites``."""

    model_config = ConfigDict(extra="forbid")

    name: NameStr
    code: CodeStr
    timezone: TimezoneStr = DEFAULT_TIMEZONE


class SiteUpdate(BaseModel):
    """Body accepted by ``PATCH /api/v1/sites/{site_id}``.

    Only the fields present in the request are applied, so an omitted field and
    an explicit ``null`` both leave the stored value untouched.

    ``code`` is declared even though it cannot be changed. Accepting it here is
    what lets the service answer with HTTP 409 ``immutable_field`` instead of
    the generic HTTP 422 that an unexpected field would produce.
    """

    model_config = ConfigDict(extra="forbid")

    name: NameStr | None = None
    timezone: TimezoneStr | None = None
    status: StatusEnum | None = None
    code: str | None = None


class SiteRead(BaseModel):
    """Representation of a site returned by every site endpoint."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    name: str
    code: str
    timezone: str
    status: StatusEnum
    created_at: datetime
    updated_at: datetime
