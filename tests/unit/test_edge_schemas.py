"""Schema wiring for the immutable Cloud ↔ Edge v1.0 contract."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_greenhouse.edge.schemas import (
    EdgeCommandAcknowledgement,
    EdgeTelemetryEnvelope,
)

CONTRACT_ROOT = Path(__file__).parents[2] / "contracts" / "cloud-edge" / "v1"


def artifact(relative_path: str) -> dict[str, Any]:
    """Load one canonical contract example."""
    return json.loads((CONTRACT_ROOT / relative_path).read_text())


@pytest.mark.parametrize(
    "relative_path",
    [
        "examples/valid/telemetry-single.json",
        "examples/valid/telemetry-batch.json",
        "examples/valid/telemetry-command-state.json",
    ],
)
def test_valid_telemetry_examples_match_the_http_schema(relative_path: str) -> None:
    """The adapter accepts every published telemetry request."""
    EdgeTelemetryEnvelope.model_validate(artifact(relative_path))


@pytest.mark.parametrize(
    "relative_path",
    [
        "examples/invalid/telemetry-received-at.json",
        "examples/invalid/telemetry-type-mismatch.json",
        "examples/invalid/telemetry-no-data-quality.json",
    ],
)
def test_invalid_telemetry_examples_fail_the_http_schema(relative_path: str) -> None:
    """The adapter rejects every published invalid telemetry request."""
    with pytest.raises(ValidationError):
        EdgeTelemetryEnvelope.model_validate(artifact(relative_path))


@pytest.mark.parametrize(
    "relative_path",
    [
        "examples/valid/command-acknowledgement-applied.json",
        "examples/valid/command-acknowledgement-rejected.json",
    ],
)
def test_valid_acknowledgements_match_the_http_schema(relative_path: str) -> None:
    """Both terminal contract representations are accepted."""
    EdgeCommandAcknowledgement.model_validate(artifact(relative_path))
