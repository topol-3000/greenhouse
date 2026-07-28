"""Repository-level guardrails for the deliberately narrow Milestone 1 scope."""

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
PACKAGE_ROOT: Path = PROJECT_ROOT / "src" / "ai_greenhouse"
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {"telemetry", "control", "simulation", "device_registry"}
)
"""Future-milestone packages that must not exist in the M1 repository."""


def test_future_milestone_modules_do_not_exist() -> None:
    """Keep telemetry, automation, simulation and devices out of Milestone 1."""
    present: set[str] = {
        module_name
        for module_name in FORBIDDEN_MODULES
        if (PACKAGE_ROOT / module_name).exists() or (PACKAGE_ROOT / f"{module_name}.py").exists()
    }
    assert present == set()
