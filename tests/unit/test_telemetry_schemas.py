"""What the telemetry write contract refuses before the service is reached.

Whether a value fits its point is not asserted here: that rule needs the
point's ``data_type``, so it lives in the service and is covered once, through
the boundary, in ``tests/integration/telemetry``.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.points.models import DataQuality
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord


@pytest.mark.parametrize("field", ["observed_at", "received_at"])
def test_a_naive_instant_is_refused(field: str) -> None:
    """Both instants are compared against ``timestamptz`` columns.

    A naive datetime would make the comparison that decides whether a sample is
    current raise ``TypeError`` deep inside the service instead of being refused
    at the edge.
    """
    aware: datetime = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    submitted: dict[str, object] = {
        "id": uuid4(),
        "point_id": uuid4(),
        "value": 21.5,
        "observed_at": aware,
        "received_at": aware,
        "quality": DataQuality.SIMULATED,
        field: aware.replace(tzinfo=None),
    }

    with pytest.raises(ValidationError):
        TelemetrySampleRecord(**submitted)
