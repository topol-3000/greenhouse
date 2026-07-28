"""Domain failures raised by the points module.

These subclass the shared hierarchy in ``ai_greenhouse.core.exceptions`` and
carry no HTTP knowledge. ``ai_greenhouse.api.errors`` maps them to responses.

Failures about a *referenced* site or facility are not redeclared here. The
missing entity belongs to the topology module, so its failures are reused from
``ai_greenhouse.topology.exceptions``.
"""

from decimal import Decimal
from uuid import UUID

from ai_greenhouse.core.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    ParentArchivedError,
)

__all__ = [
    "FacilityNotInSiteError",
    "InvalidValueRangeError",
    "PointCodeConflictError",
    "PointFacilityArchivedError",
    "PointNotFoundError",
    "PointSiteArchivedError",
    "PointStateNotFoundError",
    "UnitNotAllowedError",
    "UnitRequiredError",
    "ValueRangeNotAllowedError",
]


class PointNotFoundError(NotFoundError):
    """No point exists with the requested identifier."""

    code = "point_not_found"

    def __init__(self, point_id: UUID) -> None:
        """Report the missing point.

        Args:
            point_id: The identifier that could not be resolved.
        """
        super().__init__("Point not found", details={"point_id": str(point_id)})


class PointStateNotFoundError(NotFoundError):
    """A point exists but its current-state projection does not.

    This cannot happen through the API: the state row is created with the point
    in one transaction, and the primary key guarantees there is at most one.
    It is reported rather than assumed away so that a projection lost to a
    direct database edit surfaces as a precise failure instead of a crash.
    """

    code = "point_state_not_found"

    def __init__(self, point_id: UUID) -> None:
        """Report the missing projection.

        Args:
            point_id: The point whose state row is absent.
        """
        super().__init__(
            "Point state not found",
            details={"point_id": str(point_id)},
        )


class PointCodeConflictError(ConflictError):
    """Another point on the same site already uses the requested code.

    The same code on a different site is allowed, so the site is part of the
    reported details.
    """

    code = "point_code_conflict"

    def __init__(self, site_id: UUID, point_code: str) -> None:
        """Report the duplicate code within the site.

        Args:
            site_id: The site the code collides on.
            point_code: The code that is already taken there.
        """
        super().__init__(
            "Point code already exists within the site",
            details={"site_id": str(site_id), "code": point_code},
        )


class PointSiteArchivedError(ParentArchivedError):
    """A point was requested inside a site that is archived."""

    def __init__(self, site_id: UUID) -> None:
        """Report the archived parent site.

        Args:
            site_id: The archived site the point would have belonged to.
        """
        super().__init__(
            "Site is archived and cannot receive new points",
            details={"site_id": str(site_id)},
        )


class PointFacilityArchivedError(ParentArchivedError):
    """A point was requested inside a facility that is archived."""

    def __init__(self, facility_id: UUID) -> None:
        """Report the archived parent facility.

        Args:
            facility_id: The archived facility the point would have belonged to.
        """
        super().__init__(
            "Facility is archived and cannot receive new points",
            details={"facility_id": str(facility_id)},
        )


class FacilityNotInSiteError(DomainError):
    """The requested facility exists but belongs to a different site.

    Reported as 422 rather than 404: both identifiers came from the request
    body, and each resolves on its own. It is their combination that is
    unprocessable.
    """

    code = "facility_not_in_site"
    http_status = 422

    def __init__(self, site_id: UUID, facility_id: UUID) -> None:
        """Report the mismatched pair.

        Args:
            site_id: The site named by the request.
            facility_id: The facility that belongs to some other site.
        """
        super().__init__(
            "Facility does not belong to the requested site",
            details={"site_id": str(site_id), "facility_id": str(facility_id)},
        )


class UnitRequiredError(DomainError):
    """A numeric point was submitted without a unit.

    A bare number is not a measurement. Allowing ``22`` without knowing whether
    it is Celsius or Fahrenheit would make every later comparison, target and
    alert threshold ambiguous.
    """

    code = "unit_required"
    http_status = 422

    def __init__(self, data_type: str) -> None:
        """Report the missing unit.

        Args:
            data_type: The data type that requires one.
        """
        super().__init__(
            "A unit is required for this data type",
            details={"data_type": data_type},
        )


class UnitNotAllowedError(DomainError):
    """A unit was submitted for a point that cannot have one.

    A boolean point is on or off. A unit attached to it would describe nothing.
    """

    code = "unit_not_allowed"
    http_status = 422

    def __init__(self, data_type: str) -> None:
        """Report the rejected unit.

        Args:
            data_type: The data type that forbids one.
        """
        super().__init__(
            "A unit is not allowed for this data type",
            details={"data_type": data_type},
        )


class ValueRangeNotAllowedError(DomainError):
    """A value range was submitted for a point that cannot be ordered."""

    code = "range_not_allowed"
    http_status = 422

    def __init__(self, data_type: str) -> None:
        """Report the rejected range.

        Args:
            data_type: The data type that forbids ``min_value`` and
                ``max_value``.
        """
        super().__init__(
            "A value range is only allowed for numeric data types",
            details={"data_type": data_type},
        )


class InvalidValueRangeError(DomainError):
    """The resulting range would have its lower end above its upper end.

    Raised by ``PATCH``, where the two ends can arrive one at a time and the
    submitted value has to be judged against the stored one.
    """

    code = "invalid_value_range"
    http_status = 422

    def __init__(self, min_value: Decimal, max_value: Decimal) -> None:
        """Report the inverted range.

        Args:
            min_value: The resulting lower end.
            max_value: The resulting upper end, below the lower one.
        """
        super().__init__(
            "min_value must not be greater than max_value",
            details={"min_value": str(min_value), "max_value": str(max_value)},
        )
