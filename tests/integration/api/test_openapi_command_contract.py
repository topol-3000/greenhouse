"""The generated OpenAPI document, asserted as the contract a client builds from.

A customer-facing client is written against ``/openapi.json`` and not against
this repository. So the question these tests ask is not "does the backend
behave" — other modules own that — but "could someone implement the manual
command flow from the published document alone, without reading the source".

Every assertion here is therefore about the *document*: the operation exists,
its request is boolean on/off, its idempotency key is required and where it is
documented to be, its responses are declared, its read model admits a command
with no control loop, and its lifecycle and acknowledgement semantics are
written down rather than left to be inferred.
"""

from collections.abc import Callable
from typing import Any, Final, cast

import pytest
from fastapi import FastAPI

from ai_greenhouse.api.router import API_V1_PREFIX
from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings

FAKE_DATABASE_URL: Final[str] = "postgresql+asyncpg://test_user:top-secret@test-db:5432/test"

COMMANDS_PATH: Final[str] = f"{API_V1_PREFIX}/commands"
COMMAND_PATH: Final[str] = f"{API_V1_PREFIX}/commands/{{command_id}}"


@pytest.fixture
def document(build_settings: Callable[..., Settings]) -> dict[str, Any]:
    """Generate the published OpenAPI document once per test."""
    application: FastAPI = create_app(build_settings(FAKE_DATABASE_URL, app_env="test"))
    return cast(dict[str, Any], application.openapi())


def schema_of(document: dict[str, Any], name: str) -> dict[str, Any]:
    """Return one component schema by name."""
    components: dict[str, Any] = document["components"]["schemas"]
    assert name in components, sorted(components)
    return cast(dict[str, Any], components[name])


def resolve(document: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Follow one ``$ref`` into the component schemas."""
    reference: str = node["$ref"]
    return schema_of(document, reference.rsplit("/", 1)[-1])


def property_schema(document: dict[str, Any], schema: str, field: str) -> dict[str, Any]:
    """Return the declared schema of one property, following a ``$ref``."""
    node: dict[str, Any] = schema_of(document, schema)["properties"][field]
    return resolve(document, node) if "$ref" in node else node


def test_the_manual_command_operation_is_published(document: dict[str, Any]) -> None:
    """The operation exists on the public collection, under the control tag."""
    operation = document["paths"][COMMANDS_PATH]["post"]

    assert operation["tags"] == ["control"]
    assert (
        "manual" in " ".join([operation["summary"], operation.get("description", "")]).lower()
        or "boolean" in operation.get("description", "").lower()
    )


def test_the_request_body_is_the_three_documented_fields(document: dict[str, Any]) -> None:
    """Zone, target and value, and nothing a generic command submission would add."""
    body = document["paths"][COMMANDS_PATH]["post"]["requestBody"]
    schema = resolve(document, body["content"]["application/json"]["schema"])

    assert body["required"] is True
    assert set(schema["properties"]) == {
        "control_zone_id",
        "target_point_id",
        "desired_value",
    }
    assert set(schema["required"]) == {
        "control_zone_id",
        "target_point_id",
        "desired_value",
    }
    assert schema.get("additionalProperties") is False


def test_the_desired_value_is_a_boolean_and_not_arbitrary_json(
    document: dict[str, Any],
) -> None:
    """The single most important line of the document for this unit.

    A client generator reads ``boolean`` here and cannot produce a percentage, a
    speed or a free-form object — which is exactly the control this backend does
    not implement.
    """
    schema = property_schema(document, "ManualCommandCreate", "desired_value")

    assert schema["type"] == "boolean"


def test_the_idempotency_key_is_a_required_uuid_header(document: dict[str, Any]) -> None:
    """Its exact location, format, requiredness and semantics are all published."""
    parameters = document["paths"][COMMANDS_PATH]["post"]["parameters"]
    key = next(parameter for parameter in parameters if parameter["name"] == "Idempotency-Key")

    assert key["in"] == "header"
    assert key["required"] is True
    assert key["schema"]["format"] == "uuid"
    description: str = key["description"].lower()
    assert "409" in key["description"]
    assert "200" in key["description"]
    assert "never replaces" in description


def test_the_creation_responses_are_declared(document: dict[str, Any]) -> None:
    """First creation, replay and each expected refusal are all in the document."""
    responses = document["paths"][COMMANDS_PATH]["post"]["responses"]

    assert {"200", "201", "404", "409", "422"} <= set(responses)
    for status in ("200", "201"):
        schema = responses[status]["content"]["application/json"]["schema"]
        assert resolve(document, schema)["title"] == "ManualCommandAcceptanceRead"
    for status in ("404", "409", "422"):
        schema = responses[status]["content"]["application/json"]["schema"]
        assert resolve(document, schema)["title"] == "ErrorResponse"


def test_the_replay_outcome_is_part_of_the_response(document: dict[str, Any]) -> None:
    """A client can tell creation from replay without depending on the status code."""
    outcome = property_schema(document, "ManualCommandAcceptanceRead", "outcome")

    assert set(outcome["enum"]) == {"created", "existing"}


def test_the_command_details_operation_declares_its_404(document: dict[str, Any]) -> None:
    """A stale identifier is a documented answer, not a surprise."""
    responses = document["paths"][COMMAND_PATH]["get"]["responses"]

    assert "404" in responses
    schema = responses["404"]["content"]["application/json"]["schema"]
    assert resolve(document, schema)["title"] == "ErrorResponse"


def test_the_read_model_admits_a_command_with_no_control_loop(
    document: dict[str, Any],
) -> None:
    """The defect that blocked the customer portal, asserted in the document.

    ``control_loop_id`` and ``trigger_sample_id`` are nullable, so a generated
    client no longer treats every command as automatic; ``source``, the zone and
    the reported point are present, so it can tell which kind it is holding.
    """
    command = schema_of(document, "CommandRead")

    assert {
        "id",
        "source",
        "idempotency_key",
        "control_zone_id",
        "control_loop_id",
        "trigger_sample_id",
        "target_point_id",
        "reported_point_id",
        "desired_value",
        "state",
        "issued_at",
        "acknowledged_at",
        "executed_at",
        "rejection_reason",
        "created_at",
    } <= set(command["properties"])
    for field in ("control_loop_id", "trigger_sample_id"):
        assert {"type": "null"} in command["properties"][field]["anyOf"], field
    assert property_schema(document, "CommandRead", "desired_value")["type"] == "boolean"
    assert set(property_schema(document, "CommandRead", "source")["enum"]) == {
        "control_loop",
        "manual",
    }


def test_the_lifecycle_enum_and_its_terminal_states_are_documented(
    document: dict[str, Any],
) -> None:
    """The states, and — in prose a client can read — which of them are terminal."""
    state = property_schema(document, "CommandRead", "state")
    described: str = schema_of(document, "CommandRead")["description"].lower()

    assert set(state["enum"]) == {"pending", "applied", "rejected"}
    assert "non-terminal" in described
    assert "terminal" in described
    assert "acknowledged_at" in described


def test_the_document_separates_the_desired_value_from_the_reported_state(
    document: dict[str, Any],
) -> None:
    """The confusion most likely to produce a wrong UI is addressed in the document."""
    described: str = schema_of(document, "CommandRead")["description"].lower()

    assert "request" in described
    assert "reported point" in described


def test_the_rejection_reason_is_typed(document: dict[str, Any]) -> None:
    """A client can branch on ``code`` because ``code`` has a published shape."""
    reason = property_schema(document, "CommandRejectionReason", "code")
    fields = schema_of(document, "CommandRejectionReason")["properties"]

    assert set(fields) == {"code", "message"}
    assert reason["type"] == "string"
    assert reason["pattern"] == "^[a-z][a-z0-9_]*$"


def test_the_command_list_publishes_every_filter(document: dict[str, Any]) -> None:
    """Zone, target, source and the idempotency-key recovery lookup are all listed."""
    parameters = document["paths"][COMMANDS_PATH]["get"]["parameters"]
    declared = {parameter["name"] for parameter in parameters}

    assert {
        "control_zone_id",
        "control_loop_id",
        "trigger_sample_id",
        "target_point_id",
        "source",
        "idempotency_key",
        "limit",
    } <= declared


def test_the_configuration_document_publishes_the_reported_point(
    document: dict[str, Any],
) -> None:
    """The discovery step comes before the command, so it is in the document too."""
    configuration = schema_of(document, "ConfigurationPoint")
    point = schema_of(document, "PointRead")
    creation = schema_of(document, "PointCreate")

    assert "reported_point_id" in configuration["properties"]
    assert "reported_point_id" in point["properties"]
    assert "reported_point_id" in creation["properties"]
