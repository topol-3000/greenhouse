"""Independent validation of the published Cloud ↔ Edge contract artifacts.

This suite intentionally imports no ``ai_greenhouse`` module. A separate Edge
or test repository can copy the versioned artifact directory and run the same
Draft 2020-12 checks without access to cloud internals.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

CONTRACT_ROOT = Path(__file__).parents[2] / "contracts" / "cloud-edge" / "v1"
MANIFEST_PATH = CONTRACT_ROOT / "manifest.json"


def load_json(relative_path: str | Path) -> Any:
    """Load one contract artifact relative to the v1 root."""
    return json.loads((CONTRACT_ROOT / relative_path).read_text())


def iter_manifest_examples(
    manifest: dict[str, Any],
    example_key: str,
    schema_key: str,
) -> Iterator[tuple[str, str]]:
    """Yield example/schema pairs declared by every operation."""
    for operation in manifest["operations"].values():
        schema_path = operation.get(schema_key)
        for example_path in operation.get(example_key, []):
            if schema_path is None:
                raise AssertionError(f"{example_path} has no {schema_key}")
            yield example_path, schema_path


def validator_for(schema_path: str) -> Draft202012Validator:
    """Build a strict validator for one published schema."""
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def property_names(value: Any) -> Iterator[str]:
    """Yield object keys recursively from a JSON artifact."""
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from property_names(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from property_names(nested)


def test_manifest_publishes_the_complete_v1_operation_set() -> None:
    """The artifact index is stable and usable without cloud package imports."""
    manifest = load_json(MANIFEST_PATH.relative_to(CONTRACT_ROOT))

    assert manifest["version"] == "1.0"
    assert set(manifest["operations"]) == {
        "telemetry_ingestion",
        "command_retrieval",
        "command_acknowledgement",
        "error",
    }


@pytest.mark.parametrize(
    ("example_path", "schema_path"),
    [
        *iter_manifest_examples(load_json("manifest.json"), "valid_requests", "request_schema"),
        *iter_manifest_examples(load_json("manifest.json"), "valid_responses", "response_schema"),
    ],
)
def test_published_valid_examples_match_their_schema(
    example_path: str,
    schema_path: str,
) -> None:
    """Every canonical success and error example is schema-valid."""
    validator_for(schema_path).validate(load_json(example_path))


@pytest.mark.parametrize(
    ("example_path", "schema_path"),
    list(
        iter_manifest_examples(
            load_json("manifest.json"),
            "invalid_requests",
            "request_schema",
        )
    ),
)
def test_published_invalid_telemetry_examples_are_refused(
    example_path: str,
    schema_path: str,
) -> None:
    """Each named invalid telemetry envelope fails its canonical schema."""
    with pytest.raises(ValidationError):
        validator_for(schema_path).validate(load_json(example_path))


def test_contract_schema_vocabulary_has_no_producer_specific_fields() -> None:
    """Schema field names stay generic even though ``simulated`` is a quality."""
    schema_keys = {
        key
        for schema_path in (CONTRACT_ROOT / "schemas").glob("*.json")
        for key in property_names(load_json(schema_path.relative_to(CONTRACT_ROOT)))
    }

    assert not any("simulation" in key for key in schema_keys)
