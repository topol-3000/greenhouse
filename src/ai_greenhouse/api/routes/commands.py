"""HTTP access to commands, and the one boundary that creates a manual one.

A command has two possible authors. A control loop decides one from accepted
telemetry and no client can ask for that: it stays a consequence of a
measurement, which is what keeps "the fan follows the policy" true. A
customer-facing client can ask for the other, through ``POST`` below, for one
explicitly configured actuator and one boolean value.

Both kinds are written ``pending`` and reach the growbox through the same
gateway resolution, the same Edge polling and the same acknowledgement. This
service never talks to a device, and accepting a request here says nothing about
whether the actuator has moved.

Like telemetry history, and unlike the paged collections, the list carries no
total: it is a bounded newest-first window over an append-only table.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.errors import ErrorResponse
from ai_greenhouse.control.models import Command, CommandSource
from ai_greenhouse.control.schemas import (
    CommandListRead,
    CommandRead,
    ManualCommandAcceptanceRead,
    ManualCommandCreate,
    ManualCommandOutcome,
)
from ai_greenhouse.control.service import CommandService

DEFAULT_COMMAND_LIMIT: int = 100
MIN_COMMAND_LIMIT: int = 1
MAX_COMMAND_LIMIT: int = 1000

IDEMPOTENCY_HEADER: str = "Idempotency-Key"
"""Where a manual command's client-supplied key travels.

A header rather than a body field, so a client retrying a request it never saw
the answer to repeats exactly what it sent without having to reconstruct the
body around the key.
"""

IDEMPOTENCY_HEADER_DESCRIPTION: str = (
    "Required client-supplied UUID identifying one logical manual command."
    " Repeating it with the same control_zone_id, target_point_id and"
    " desired_value returns the stored command with HTTP 200 and creates"
    " nothing; repeating it with a different one answers HTTP 409"
    " idempotency_key_conflict. The server never replaces a supplied key."
)

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}
"""The common error envelope, declared where a client has to branch on it."""

router: APIRouter = APIRouter(prefix="/commands", tags=["control"])


async def get_command_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommandService:
    """Build the command service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``CommandService`` sharing the request's transaction.
    """
    return CommandService(session)


CommandServiceDep = Annotated[CommandService, Depends(get_command_service)]


@router.post(
    "",
    response_model=ManualCommandAcceptanceRead,
    responses={
        status.HTTP_200_OK: {"model": ManualCommandAcceptanceRead},
        status.HTTP_201_CREATED: {"model": ManualCommandAcceptanceRead},
        **ERROR_RESPONSES,
    },
)
async def create_manual_command(
    payload: ManualCommandCreate,
    response: Response,
    service: CommandServiceDep,
    idempotency_key: Annotated[
        UUID,
        Header(alias=IDEMPOTENCY_HEADER, description=IDEMPOTENCY_HEADER_DESCRIPTION),
    ],
) -> ManualCommandAcceptanceRead:
    """Ask one configured boolean actuator for one state, exactly once.

    The target has to be an active boolean control point that is assigned to the
    named zone in the ``control_output`` role and names a reported status point.
    Every one of those is read from persisted configuration; none of it is
    inferred from a code, a name or a metric.

    HTTP 201 means the command was created, HTTP 200 that the key already named
    it and nothing was written a second time. Neither means the actuator moved:
    the command is ``pending`` until the Edge reports it ``applied`` or
    ``rejected``, and the reported point's own state is what says what the
    hardware is actually doing.

    Args:
        payload: The zone, the point and the boolean value asked for.
        response: The response whose status distinguishes creation from replay.
        service: The command service for this request.
        idempotency_key: The client's ``Idempotency-Key`` header.

    Returns:
        The stored command and whether this request created it.
    """
    command, created = await service.create_manual_command(
        payload,
        idempotency_key=str(idempotency_key),
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return ManualCommandAcceptanceRead(
        outcome=ManualCommandOutcome.CREATED if created else ManualCommandOutcome.EXISTING,
        command=CommandRead.model_validate(command),
    )


@router.get("", response_model=CommandListRead, responses=ERROR_RESPONSES)
async def list_commands(
    service: CommandServiceDep,
    control_zone_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the commands of one control zone."),
    ] = None,
    control_loop_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the commands of one control loop."),
    ] = None,
    trigger_sample_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the commands one measurement caused."),
    ] = None,
    target_point_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the commands addressed to one point."),
    ] = None,
    source: Annotated[
        CommandSource | None,
        Query(description="Restrict the result to one author of commands."),
    ] = None,
    idempotency_key: Annotated[
        UUID | None,
        Query(
            description=(
                "Resolve the one manual command a client-supplied key identifies."
                " The key is unique, so the answer carries zero or one command:"
                " this is how a client that lost a creation response finds out"
                " whether its command exists."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=MIN_COMMAND_LIMIT,
            le=MAX_COMMAND_LIMIT,
            description="Maximum number of commands to return.",
        ),
    ] = DEFAULT_COMMAND_LIMIT,
) -> CommandListRead:
    """Return a bounded, deterministic newest-first window of commands.

    Every filter is an exact match, and several may be combined. Ordering is
    ``created_at DESC, id DESC`` and is enforced by the query itself, so
    repeated calls return the same window.

    Args:
        service: The command service for this request.
        control_zone_id: Restricts the result to one zone when given.
        control_loop_id: Restricts the result to one loop when given.
        trigger_sample_id: Restricts the result to one trigger when given.
        target_point_id: Restricts the result to one commanded point when given.
        source: Restricts the result to one author when given.
        idempotency_key: Resolves the one command a key identifies when given.
        limit: Maximum number of commands to return.

    Returns:
        The matching commands in ``created_at DESC, id DESC`` order.
    """
    commands: list[Command] = await service.list_commands(
        control_zone_id=control_zone_id,
        control_loop_id=control_loop_id,
        trigger_sample_id=trigger_sample_id,
        target_point_id=target_point_id,
        source=source,
        idempotency_key=None if idempotency_key is None else str(idempotency_key),
        limit=limit,
    )
    return CommandListRead(items=[CommandRead.model_validate(command) for command in commands])


@router.get(
    "/{command_id}",
    response_model=CommandRead,
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def get_command(command_id: UUID, service: CommandServiceDep) -> CommandRead:
    """Read one command and the identifiers its chain is followed by.

    Args:
        command_id: The command to read.
        service: The command service for this request.

    Returns:
        The stored command.
    """
    command: Command = await service.get_command(command_id)
    return CommandRead.model_validate(command)
