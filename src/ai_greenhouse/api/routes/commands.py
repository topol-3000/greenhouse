"""Read-only HTTP access to command history.

There is deliberately no ``POST``. A command is what accepted telemetry led to,
so offering a client a way to create one would put a second author on the fan
state and make "the fan follows the policy" untrue.

Like telemetry history, and unlike the paged collections, the list carries no
total: it is a bounded newest-first window over an append-only table.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.control.models import Command
from ai_greenhouse.control.schemas import CommandListRead, CommandRead
from ai_greenhouse.control.service import CommandService

DEFAULT_COMMAND_LIMIT: int = 100
MIN_COMMAND_LIMIT: int = 1
MAX_COMMAND_LIMIT: int = 1000

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


@router.get("", response_model=CommandListRead)
async def list_commands(
    service: CommandServiceDep,
    control_loop_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the commands of one control loop."),
    ] = None,
    trigger_sample_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the commands one measurement caused."),
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

    Args:
        service: The command service for this request.
        control_loop_id: Restricts the result to one loop when given.
        trigger_sample_id: Restricts the result to one trigger when given.
        limit: Maximum number of commands to return.

    Returns:
        The matching commands in ``created_at DESC, id DESC`` order.
    """
    commands: list[Command] = await service.list_commands(
        control_loop_id=control_loop_id,
        trigger_sample_id=trigger_sample_id,
        limit=limit,
    )
    return CommandListRead(items=[CommandRead.model_validate(command) for command in commands])


@router.get("/{command_id}", response_model=CommandRead)
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
