"""Run the dashboard's JavaScript tests from the Python suite.

The page is the one part of this repository that is not Python, and until now
nothing executed it: the tests around it asserted the endpoints it reads and the
assets it is served as, which cannot fail when a mapping in ``dashboard.js``
breaks. The files beside this one close that gap using Node's own test runner
and nothing else — no ``package.json``, no lockfile, no jsdom and no frontend
framework.

The runtime is optional on purpose. The API image is a Python image and has no
Node in it, so this skips there rather than making the container carry a second
language runtime for a test. It runs wherever Node is on the path, which is the
developer machine and CI.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SUITE_DIRECTORY: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = SUITE_DIRECTORY.parents[1]

TEST_FILE_GLOB: str = "*.test.mjs"
"""Pattern Node's runner discovers the suites by."""


def test_the_dashboard_javascript_suite_passes() -> None:
    """Execute every ``*.test.mjs`` beside this file and require them all green."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH; run `node --test tests/javascript/*.test.mjs` instead")

    suites = sorted(str(path) for path in SUITE_DIRECTORY.glob(TEST_FILE_GLOB))
    assert suites, "no JavaScript suites were found"

    completed = subprocess.run(  # noqa: S603 - fixed argument vector, no shell
        [node, "--test", *suites],
        capture_output=True,
        cwd=PROJECT_ROOT,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
