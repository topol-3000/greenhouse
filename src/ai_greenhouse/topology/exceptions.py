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
    "AssignmentExistsError",
    "AssignmentNotFoundError",
    "AssignmentPointArchivedError",
    "AssignmentZoneArchivedError",
    "ControlZoneCodeConflictError",
    "ControlZoneFacilityArchivedError",
    "ControlZoneFacilityImmutableError",
    "ControlZoneNotFoundError",
    "CrossFacilityAssignmentError",
    "CrossSiteAssignmentError",
    "FacilityCodeConflictError",
    "FacilityNotFoundError",
    "FacilitySiteArchivedError",
    "FacilitySiteImmutableError",
    "ParentFacilityNotFoundError",
    "ParentSiteNotFoundError",
    "PrimaryMeasurementExistsError",
    "RoleKindMismatchError",
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


class AssignmentNotFoundError(NotFoundError):
    """No zone-point assignment exists with the requested identifier.

    Also raised when the assignment exists but belongs to a different zone than
    the one in the path: from that zone's point of view there is nothing to
    remove, and answering otherwise would let one zone delete another's links.
    """

    code = "assignment_not_found"

    def __init__(self, control_zone_id: UUID, assignment_id: UUID) -> None:
        """Report the missing assignment.

        Args:
            control_zone_id: The zone the assignment was looked for in.
            assignment_id: The identifier that could not be resolved there.
        """
        super().__init__(
            "Zone point assignment not found",
            details={
                "control_zone_id": str(control_zone_id),
                "assignment_id": str(assignment_id),
            },
        )


class AssignmentZoneArchivedError(ParentArchivedError):
    """A point was assigned to a zone that is archived."""

    def __init__(self, control_zone_id: UUID) -> None:
        """Report the archived zone.

        Args:
            control_zone_id: The archived zone the assignment was aimed at.
        """
        super().__init__(
            "Control zone is archived and cannot receive new point assignments",
            details={"control_zone_id": str(control_zone_id)},
        )


class AssignmentPointArchivedError(ParentArchivedError):
    """An archived point was assigned to a zone.

    Reported with the same ``parent_archived`` code as an archived zone: the
    reason for the refusal is the same one, and the details say which of the two
    ends is retired.
    """

    def __init__(self, point_id: UUID) -> None:
        """Report the archived point.

        Args:
            point_id: The archived point the assignment named.
        """
        super().__init__(
            "Point is archived and cannot be assigned to a control zone",
            details={"point_id": str(point_id)},
        )


class CrossSiteAssignmentError(ConflictError):
    """The point and the zone belong to different sites.

    This is the invariant the milestone's Definition of Done names explicitly.
    The zone's site is not stored on the zone; it is reached through the zone's
    facility, which is why both identifiers are reported.
    """

    code = "cross_site_assignment"

    def __init__(self, zone_site_id: UUID, point_site_id: UUID) -> None:
        """Report the two sites that failed to match.

        Args:
            zone_site_id: The site reached through the zone's facility.
            point_site_id: The site the point belongs to.
        """
        super().__init__(
            "A control zone and a point of another site cannot be linked",
            details={
                "zone_site_id": str(zone_site_id),
                "point_site_id": str(point_site_id),
            },
        )


class CrossFacilityAssignmentError(ConflictError):
    """The point belongs to a facility other than the zone's.

    A point without a facility is not refused: it belongs to the site as a
    whole — an outdoor temperature, a mains water pressure — and any zone on
    that site may read it.
    """

    code = "cross_facility_assignment"

    def __init__(self, zone_facility_id: UUID, point_facility_id: UUID) -> None:
        """Report the two facilities that failed to match.

        Args:
            zone_facility_id: The facility the zone belongs to.
            point_facility_id: The facility the point belongs to.
        """
        super().__init__(
            "A control zone and a point of another facility cannot be linked",
            details={
                "zone_facility_id": str(zone_facility_id),
                "point_facility_id": str(point_facility_id),
            },
        )


class RoleKindMismatchError(ConflictError):
    """The requested role does not suit the point's kind.

    A measurement point cannot be a zone's ``control_output`` and a control
    point cannot be its ``primary_measurement``. The allowed combinations are
    declared once, in
    :data:`~ai_greenhouse.topology.service.ROLE_ALLOWED_POINT_KINDS`.
    """

    code = "role_kind_mismatch"

    def __init__(self, role: str, point_kind: str, allowed_point_kinds: list[str]) -> None:
        """Report the rejected pair and what the role does accept.

        Args:
            role: The requested role.
            point_kind: The kind of the point it was requested for.
            allowed_point_kinds: The kinds of point that role accepts, sorted.
        """
        super().__init__(
            "This role is not allowed for this kind of point",
            details={
                "role": role,
                "point_kind": point_kind,
                "allowed_point_kinds": allowed_point_kinds,
            },
        )


class PrimaryMeasurementExistsError(ConflictError):
    """The zone already has a point assigned as its primary measurement.

    A zone is controlled against one process variable. A second primary
    measurement would leave "the value of this zone" ambiguous for every rule
    that later reads it.
    """

    code = "primary_measurement_exists"

    def __init__(self, control_zone_id: UUID, assigned_point_id: UUID) -> None:
        """Report the zone and the point that already holds the role.

        Args:
            control_zone_id: The zone that is already covered.
            assigned_point_id: The point currently assigned as its primary
                measurement.
        """
        super().__init__(
            "The control zone already has a primary measurement",
            details={
                "control_zone_id": str(control_zone_id),
                "assigned_point_id": str(assigned_point_id),
            },
        )


class AssignmentExistsError(ConflictError):
    """The point is already assigned to the zone under that exact role.

    The same point in the same zone under a *different* role is allowed and is
    not reported here.
    """

    code = "assignment_exists"

    def __init__(self, control_zone_id: UUID, point_id: UUID, role: str) -> None:
        """Report the duplicate link.

        Args:
            control_zone_id: The zone the point is already assigned to.
            point_id: The point that is already assigned.
            role: The role it already holds there.
        """
        super().__init__(
            "The point is already assigned to this control zone with this role",
            details={
                "control_zone_id": str(control_zone_id),
                "point_id": str(point_id),
                "role": role,
            },
        )
