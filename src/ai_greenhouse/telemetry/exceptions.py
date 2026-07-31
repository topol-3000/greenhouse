"""Domain failures raised by the telemetry module.

These subclass the shared hierarchy in ``ai_greenhouse.core.exceptions`` and
carry no HTTP knowledge. ``ai_greenhouse.api.errors`` maps them to responses for
whichever endpoint drives a producer; today that is the public Cloud ↔ Edge
telemetry boundary, which translates them into its own closed error contract.

A sample naming a point that does not exist is not redeclared here: the missing
entity belongs to the points module, so
``ai_greenhouse.points.exceptions.ReferencedPointNotFoundError`` is reused.
"""

from uuid import UUID

from ai_greenhouse.core.exceptions import DomainError, ParentArchivedError

__all__ = [
    "ArchivedPointError",
    "InvalidTelemetryWindowError",
    "TelemetryValueTypeError",
]


class ArchivedPointError(ParentArchivedError):
    """A sample was submitted for a point that is no longer active.

    Archiving a point is how it is retired, and a retired point does not start
    carrying values again. The history it already has stays readable — this
    refuses the *new* sample, not the old ones.
    """

    def __init__(self, point_id: UUID) -> None:
        """Report the archived point.

        Args:
            point_id: The archived point the sample was addressed to.
        """
        super().__init__(
            "Point is archived and cannot receive telemetry",
            details={"point_id": str(point_id)},
        )


class InvalidTelemetryWindowError(DomainError):
    """The requested history window ends before it begins."""

    code = "invalid_telemetry_window"
    http_status = 422

    def __init__(self) -> None:
        """Report an inverted inclusive time window."""
        super().__init__("from must not be later than to")


class TelemetryValueTypeError(DomainError):
    """The submitted value contradicts the point's ``data_type``.

    ``jsonb`` accepts anything, so this check is the only thing standing between
    a point declared ``float`` and a history of strings. ``null`` fails it too:
    absence of data is the ``no_data`` state of a point, and never a sample
    carrying nothing.
    """

    code = "telemetry_value_type_mismatch"
    http_status = 422

    def __init__(self, point_id: UUID, data_type: str, value: object) -> None:
        """Report the rejected value.

        Args:
            point_id: The point whose declared type the value contradicts.
            data_type: The type that point declares.
            value: The rejected value. Only its Python type reaches the details;
                the value itself is a producer's payload and is not echoed back.
        """
        super().__init__(
            "Telemetry value does not match the point's data type",
            details={
                "point_id": str(point_id),
                "data_type": data_type,
                "value_type": type(value).__name__,
            },
        )
