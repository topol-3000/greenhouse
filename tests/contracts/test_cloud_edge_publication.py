"""The published contract artifacts carry a verifiable publication digest.

Like ``test_cloud_edge_contracts``, this suite imports no ``ai_greenhouse``
module. It asserts the property an independent Edge consumer depends on: the
version directory it vendored can be proved byte-identical to what cloud
published, with a Draft 2020-12 validator and a SHA-256 implementation and
nothing else.

Regenerate the index with::

    uv run python scripts/publish_contract_checksums.py
"""

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

CONTRACT_ROOT = Path(__file__).parents[2] / "contracts" / "cloud-edge" / "v1"
CHECKSUM_PATH = CONTRACT_ROOT / "checksums.json"
MANIFEST_PATH = CONTRACT_ROOT / "manifest.json"


def load_json(path: Path) -> Any:
    """Load one contract artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def covered_artifacts() -> set[str]:
    """Return every machine-readable artifact the index must cover."""
    return {
        str(path.relative_to(CONTRACT_ROOT))
        for path in CONTRACT_ROOT.rglob("*.json")
        if path.name != CHECKSUM_PATH.name
    }


def manifest_references() -> set[str]:
    """Return every artifact path the manifest points a consumer at."""
    references: set[str] = set()
    for operation in load_json(MANIFEST_PATH)["operations"].values():
        for key in ("request_schema", "response_schema"):
            if key in operation:
                references.add(operation[key])
        for key in ("valid_requests", "invalid_requests", "valid_responses"):
            references.update(operation.get(key, []))
    return references


def test_the_publication_index_identifies_the_contract_it_describes() -> None:
    """A consumer can tell which contract and version a digest set belongs to."""
    index = load_json(CHECKSUM_PATH)
    manifest = load_json(MANIFEST_PATH)

    assert index["algorithm"] == "sha256"
    assert index["contract"] == manifest["contract"]
    assert index["version"] == manifest["version"] == "1.0"


def test_every_published_artifact_is_indexed_exactly_once() -> None:
    """An added or removed artifact fails here rather than silently shipping."""
    assert set(load_json(CHECKSUM_PATH)["artifacts"]) == covered_artifacts()


def test_indexed_digests_match_the_published_bytes() -> None:
    """Editing a published v1.0 artifact is contract drift and must fail."""
    drifted = {
        relative: sha256((CONTRACT_ROOT / relative).read_bytes()).hexdigest()
        for relative, expected in load_json(CHECKSUM_PATH)["artifacts"].items()
        if sha256((CONTRACT_ROOT / relative).read_bytes()).hexdigest() != expected
    }

    assert drifted == {}, (
        "published v1.0 artifacts changed; v1.0 is immutable, so either revert "
        "the edit or publish a new version"
    )


def test_the_manifest_points_only_at_indexed_artifacts() -> None:
    """A consumer following the manifest never reaches an unverifiable file."""
    assert manifest_references() <= set(load_json(CHECKSUM_PATH)["artifacts"])
