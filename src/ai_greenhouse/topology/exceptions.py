"""Domain failures raised by the topology module.

These subclass the shared hierarchy in ``ai_greenhouse.core.exceptions`` and
carry no HTTP knowledge. ``ai_greenhouse.api.errors`` maps them to responses.
"""

from uuid import UUID

from ai_greenhouse.core.exceptions import (
    ConflictError,
    NotFoundError,
    ParentArchivedError,
    ReferenceError,
)

__all__ = [
    "ControlZoneCodeConflictError",
    "ControlZoneFacilityArchivedError",
    "ControlZoneFacilityImmutableError",
    "ControlZoneNotFoundError",
    "FacilityCodeConflictError",
    "FacilityNotFoundError",
    "FacilitySiteArchivedError",
    "FacilitySiteImmutableError",
    "ParentFacilityNotFoundError",
    "ParentSiteNotFoundError",
    "SiteCodeConflictError",
    "SiteNotFoundError",
]


class SiteNotFoundError(NotFoundError):
    """No site exists with the requested identifier."""

    code = "site_not_found"

    def __init__(self, site_id: UUID) -> None:
        """Report the missing site.

        Args:
            site_id: The identifier that could not be resolved.
        """
        super().__init__("Site not found", details={"site_id": str(site_id)})


class SiteCodeConflictError(ConflictError):
    """Another site already uses the requested code."""

    code = "site_code_conflict"

    def __init__(self, site_code: str) -> None:
        """Report the duplicate code.

        Args:
            site_code: The code that is already taken.
        """
        super().__init__("Site code already exists", details={"code": site_code})


class ParentSiteNotFoundError(ReferenceError):
    """A request body references a site that does not exist.

    Distinct from :class:`SiteNotFoundError`: the identifier came from the body
    rather than from the path, so the request is unprocessable (HTTP 422) rather
    than addressed at a missing resource (HTTP 404).
    """

    code = "site_not_found"

    def __init__(self, site_id: UUID) -> None:
        """Report the missing parent site.

        Args:
            site_id: The identifier that could not be resolved.
        """
        super().__init__("Site not found", details={"site_id": str(site_id)})


class FacilitySiteArchivedError(ParentArchivedError):
    """A facility was requested inside a site that is archived."""

    def __init__(self, site_id: UUID) -> None:
        """Report the archived parent site.

        Args:
            site_id: The archived site the facility would have belonged to.
        """
        super().__init__(
            "Site is archived and cannot receive new facilities",
            details={"site_id": str(site_id)},
        )


class FacilityNotFoundError(NotFoundError):
    """No facility exists with the requested identifier."""

    code = "facility_not_found"

    def __init__(self, facility_id: UUID) -> None:
        """Report the missing facility.

        Args:
            facility_id: The identifier that could not be resolved.
        """
        super().__init__("Facility not found", details={"facility_id": str(facility_id)})


class FacilityCodeConflictError(ConflictError):
    """Another facility on the same site already uses the requested code.

    The same code on a different site is allowed, so the site is part of the
    reported details.
    """

    code = "facility_code_conflict"

    def __init__(self, site_id: UUID, facility_code: str) -> None:
        """Report the duplicate code within the site.

        Args:
            site_id: The site the code collides on.
            facility_code: The code that is already taken there.
        """
        super().__init__(
            "Facility code already exists within the site",
            details={"site_id": str(site_id), "code": facility_code},
        )


class FacilitySiteImmutableError(ConflictError):
    """A ``PATCH`` tried to move a facility to another site.

    Kept separate from ``immutable_field`` because the refusal is not about a
    field being frozen but about the operation being administrative: moving a
    facility has to migrate its zones and points too.
    """

    code = "facility_site_immutable"

    def __init__(self, facility_id: UUID) -> None:
        """Report the refused move.

        Args:
            facility_id: The facility the request tried to move.
        """
        super().__init__(
            "A facility cannot be moved between sites; "
            "moving one is a separate administrative operation",
            details={"facility_id": str(facility_id)},
        )


class ParentFacilityNotFoundError(ReferenceError):
    """A request body references a facility that does not exist.

    Distinct from :class:`FacilityNotFoundError`: the identifier came from the
    body rather than from the path, so the request is unprocessable (HTTP 422)
    rather than addressed at a missing resource (HTTP 404). Both report the same
    ``facility_not_found`` code, because the missing entity is the same one.
    """

    code = "facility_not_found"

    def __init__(self, facility_id: UUID) -> None:
        """Report the missing parent facility.

        Args:
            facility_id: The identifier that could not be resolved.
        """
        super().__init__("Facility not found", details={"facility_id": str(facility_id)})


class ControlZoneFacilityArchivedError(ParentArchivedError):
    """A control zone was requested inside a facility that is archived."""

    def __init__(self, facility_id: UUID) -> None:
        """Report the archived parent facility.

        Args:
            facility_id: The archived facility the zone would have belonged to.
        """
        super().__init__(
            "Facility is archived and cannot receive new control zones",
            details={"facility_id": str(facility_id)},
        )


class ControlZoneNotFoundError(NotFoundError):
    """No control zone exists with the requested identifier."""

    code = "control_zone_not_found"

    def __init__(self, control_zone_id: UUID) -> None:
        """Report the missing control zone.

        Args:
            control_zone_id: The identifier that could not be resolved.
        """
        super().__init__(
            "Control zone not found",
            details={"control_zone_id": str(control_zone_id)},
        )


class ControlZoneCodeConflictError(ConflictError):
    """Another zone in the same facility already uses the requested code.

    The same code in a different facility is allowed, so the facility is part of
    the reported details.
    """

    code = "control_zone_code_conflict"

    def __init__(self, facility_id: UUID, zone_code: str) -> None:
        """Report the duplicate code within the facility.

        Args:
            facility_id: The facility the code collides in.
            zone_code: The code that is already taken there.
        """
        super().__init__(
            "Control zone code already exists within the facility",
            details={"facility_id": str(facility_id), "code": zone_code},
        )


class ControlZoneFacilityImmutableError(ConflictError):
    """A ``PATCH`` tried to move a control zone to another facility.

    Kept separate from ``immutable_field`` because the refusal is not about a
    field being frozen but about the invariant it protects: a zone belongs to
    exactly one facility, and the points assigned to it are scoped by that
    facility too. Re-parenting a zone would silently break those assignments.
    """

    code = "zone_facility_immutable"

    def __init__(self, control_zone_id: UUID) -> None:
        """Report the refused move.

        Args:
            control_zone_id: The zone the request tried to move.
        """
        super().__init__(
            "A control zone cannot be moved between facilities; "
            "a zone belongs to exactly one facility for its whole life",
            details={"control_zone_id": str(control_zone_id)},
        )
