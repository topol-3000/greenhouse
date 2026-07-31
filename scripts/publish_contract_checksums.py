"""Regenerate the published Cloud ↔ Edge contract checksum index.

The cloud repository owns the contract. An independent Edge consumer vendors a
copy of one version directory and has to be able to prove, offline, that its
copy is byte-identical to what cloud published. ``checksums.json`` is that
proof: it lists every consumable artifact of one version with its SHA-256.

Only the machine-readable artifacts are covered — the manifest, the schemas and
the examples. ``README.md`` is prose that a patch release may clarify without
changing validation, and covering it would turn a wording fix into a consumer
compatibility failure.

Usage:
    uv run python scripts/publish_contract_checksums.py [version_directory]
"""

import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Final

CONTRACT_ROOT: Final[Path] = Path(__file__).resolve().parents[1] / "contracts" / "cloud-edge" / "v1"
"""Default version directory to index."""

CHECKSUM_FILE: Final[str] = "checksums.json"
"""Name of the generated index, inside the version directory it describes."""

COVERED_SUFFIX: Final[str] = ".json"
"""Only machine-readable artifacts are covered; prose is not."""


def covered_artifacts(root: Path) -> list[Path]:
    """Return every artifact the index covers, in stable relative order.

    Args:
        root: The version directory, for example ``contracts/cloud-edge/v1``.

    Returns:
        Paths relative to ``root``, sorted so the index is reproducible.
    """
    return sorted(
        path.relative_to(root)
        for path in root.rglob(f"*{COVERED_SUFFIX}")
        if path.name != CHECKSUM_FILE
    )


def digest(path: Path) -> str:
    """Return the SHA-256 of one artifact's exact bytes.

    Args:
        path: The file to digest.

    Returns:
        The lowercase hexadecimal digest.
    """
    return sha256(path.read_bytes()).hexdigest()


def build_index(root: Path) -> dict[str, object]:
    """Build the complete checksum index of one contract version.

    Args:
        root: The version directory to index.

    Returns:
        The index document, ready to be written as ``checksums.json``.
    """
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return {
        "contract": manifest["contract"],
        "version": manifest["version"],
        "algorithm": "sha256",
        "artifacts": {
            str(relative): digest(root / relative) for relative in covered_artifacts(root)
        },
    }


def main(argv: list[str]) -> int:
    """Write the index for the requested version directory.

    Args:
        argv: Command-line arguments after the program name.

    Returns:
        The process exit status.
    """
    root = Path(argv[0]).resolve() if argv else CONTRACT_ROOT
    (root / CHECKSUM_FILE).write_text(
        json.dumps(build_index(root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
