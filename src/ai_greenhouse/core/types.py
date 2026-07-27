"""Shared value types reused by every module schema.

Declaring the rules once keeps the ``code`` regex and the name bounds out of
individual schemas, where they would drift apart.
"""

from typing import Annotated

from pydantic import StringConstraints

CODE_PATTERN = r"^[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?$"
"""Slug: lowercase, starts and ends alphanumeric, 1-63 characters."""

MAX_CODE_LENGTH = 63
MIN_NAME_LENGTH = 1
MAX_NAME_LENGTH = 200

CodeStr = Annotated[
    str,
    StringConstraints(pattern=CODE_PATTERN, max_length=MAX_CODE_LENGTH),
]
"""Stable machine-readable identifier, for example ``basil-growbox``."""

NameStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=MIN_NAME_LENGTH,
        max_length=MAX_NAME_LENGTH,
    ),
]
"""Human-readable label, stripped of surrounding whitespace."""
