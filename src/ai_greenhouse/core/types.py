"""Shared value types reused by every module schema.

Declaring the rules once keeps the ``code`` regex and the name bounds out of
individual schemas, where they would drift apart.
"""

from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, StringConstraints

MAX_CODE_LENGTH: int = 63
MIN_NAME_LENGTH: int = 1
MAX_NAME_LENGTH: int = 200
MIN_TIMEZONE_LENGTH: int = 1
MAX_TIMEZONE_LENGTH: int = 64
MIN_UNIT_LENGTH: int = 1
MAX_UNIT_LENGTH: int = 32

DEFAULT_TIMEZONE: str = "UTC"
"""Fallback timezone for entities that do not declare one."""


def slug_pattern(max_length: int) -> str:
    """Build the slug regex bounded at ``max_length`` characters.

    The shape of a slug is one rule for the whole project — lowercase, starting
    and ending alphanumeric, with hyphens and underscores between. Only its
    length differs per entity, and deriving the pattern from the bound is what
    keeps a longer code from also becoming a differently *shaped* code.

    Args:
        max_length: Longest accepted slug; at least two characters.

    Returns:
        The anchored regular expression accepting slugs of 1 to ``max_length``
        characters.
    """
    return rf"^[a-z0-9]([a-z0-9_-]{{0,{max_length - 2}}}[a-z0-9])?$"


def slug_type(max_length: int) -> Any:
    """Build a slug annotation bounded at ``max_length`` characters.

    Args:
        max_length: Longest accepted slug.

    Returns:
        An ``Annotated[str, ...]`` alias usable directly as a schema field
        annotation. The return type is untyped because an annotation object is
        not a class.
    """
    return Annotated[
        str,
        StringConstraints(pattern=slug_pattern(max_length), max_length=max_length),
    ]


def name_type(max_length: int) -> Any:
    """Build a stripped human-readable label annotation bounded at ``max_length``.

    Args:
        max_length: Longest accepted label, measured after stripping.

    Returns:
        An ``Annotated[str, ...]`` alias usable directly as a schema field
        annotation. The return type is untyped because an annotation object is
        not a class.
    """
    return Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=MIN_NAME_LENGTH,
            max_length=max_length,
        ),
    ]


CODE_PATTERN: str = slug_pattern(MAX_CODE_LENGTH)
"""Slug: lowercase, starts and ends alphanumeric, 1-63 characters."""

CodeStr = slug_type(MAX_CODE_LENGTH)
"""Stable machine-readable identifier, for example ``basil-growbox``."""

NameStr = name_type(MAX_NAME_LENGTH)
"""Human-readable label, stripped of surrounding whitespace."""

UnitStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=MIN_UNIT_LENGTH,
        max_length=MAX_UNIT_LENGTH,
    ),
]
"""Unit of measurement as written by the operator, for example ``°C`` or ``%``.

Deliberately not validated against a controlled vocabulary. A unit registry is a
domain decision of its own, and guessing one now would reject the perfectly
reasonable units a grower actually uses. Whether a unit is required at all
depends on the point's ``data_type`` and is decided in the points service.
"""


def validate_iana_timezone(value: str) -> str:
    """Reject anything the ``tzdata`` database does not know.

    The name is resolved rather than matched against a pattern, so a
    well-formed but non-existent zone such as ``Not/AZone`` is rejected too.

    Args:
        value: The candidate timezone name, for example ``Europe/Kiev``.

    Returns:
        The value unchanged, once it resolves to a known zone.

    Raises:
        ValueError: If the name is not a known IANA timezone. Pydantic turns
            this into a validation failure, which the API reports as HTTP 422.
    """
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"{value!r} is not a valid IANA timezone name") from error
    return value


TimezoneStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=MIN_TIMEZONE_LENGTH,
        max_length=MAX_TIMEZONE_LENGTH,
    ),
    AfterValidator(validate_iana_timezone),
]
"""IANA timezone name, validated against the bundled ``tzdata`` database."""
