"""There is no cloud-owned bootstrap left to run.

The cloud owns general domain configuration; it does not own a dataset. A demo
seed, a console script, an entrypoint step or a Compose command that created one
would all reintroduce the same thing, so each is asserted where it would have to
appear: in the importable package, in the packaging metadata, and in the two
files that decide what a container runs before Uvicorn.
"""

import importlib.util
import re
import tomllib
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

BOOTSTRAP_PATTERN: re.Pattern[str] = re.compile(
    r"ai_greenhouse\.seed|demo-init|demo_init|seed\s+demo|automation-demo",
)
"""Every spelling the removed seed commands were invoked by."""

ENTRYPOINT_PATHS: tuple[Path, ...] = (
    PROJECT_ROOT / "scripts" / "entrypoint.sh",
    Path("/usr/local/bin/entrypoint"),
)
"""The script in the repository, and where the image installs it.

The suite runs inside the image as well as on a checkout, and the installed copy
is the one a container actually executes, so whichever exists is asserted.
"""


def entrypoint_source() -> str:
    """Return the text of the entrypoint this environment would run.

    Returns:
        The script's contents.

    Raises:
        AssertionError: If neither the checkout nor the image carries it.
    """
    for path in ENTRYPOINT_PATHS:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise AssertionError(f"no entrypoint found at any of {ENTRYPOINT_PATHS}")


def test_no_seed_package_is_importable() -> None:
    """``ai_greenhouse.seed`` is gone, so ``python -m`` has nothing to run."""
    assert importlib.util.find_spec("ai_greenhouse") is not None
    assert importlib.util.find_spec("ai_greenhouse.seed") is None


def test_packaging_exposes_no_command_line_entry_point() -> None:
    """No console script or plugin entry point ships a bootstrap command.

    The distribution has no CLI at all, which is the strongest form of the rule:
    there is no name a `demo-init` could be added back under quietly.
    """
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert "scripts" not in project
    assert "gui-scripts" not in project
    assert "entry-points" not in project
    assert not BOOTSTRAP_PATTERN.search((PROJECT_ROOT / "pyproject.toml").read_text("utf-8"))


def test_the_container_entrypoint_migrates_and_serves_and_seeds_nothing() -> None:
    """The entrypoint waits, migrates and execs the server. Nothing writes data."""
    entrypoint = entrypoint_source()

    assert "alembic upgrade head" in entrypoint
    assert "uvicorn ai_greenhouse.app:create_app" in entrypoint
    assert BOOTSTRAP_PATTERN.search(entrypoint) is None


def test_compose_runs_no_bootstrap_command() -> None:
    """Compose starts PostgreSQL and the image's own entrypoint, and nothing else."""
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert BOOTSTRAP_PATTERN.search(compose) is None
    assert "command:" not in compose


def test_no_source_file_creates_a_demonstration_dataset() -> None:
    """No module left in ``src`` names the removed bootstrap or its commands."""
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in (PROJECT_ROOT / "src").rglob("*.py")
        if BOOTSTRAP_PATTERN.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
