"""Application service behind the public Cloud ↔ Edge adapter."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.control.automation import TelemetryIngestionService
from ai_greenhouse.control.models import Command, CommandState
from ai_greenhouse.control.repository import CommandRepository
from ai_greenhouse.edge.exceptions import (
    EdgeValidationError,
    command_acknowledgement_conflict,
    command_not_found,
    command_not_pending,
    gateway_inactive,
    gateway_not_found,
    gateway_point_forbidden,
    point_inactive,
    point_not_found,
    telemetry_message_conflict,
)
from ai_greenhouse.edge.repository import EdgeTelemetryMessageRepository
from ai_greenhouse.edge.schemas import (
    CONTRACT_VERSION,
    EdgeCommandAcknowledgement,
    EdgeCommandList,
    EdgeCommandOutcome,
    EdgePendingCommand,
    EdgeTelemetryEnvelope,
    EdgeTelemetryIngestionResult,
    EdgeTelemetryMessage,
    EdgeTelemetryResultItem,
)
from ai_greenhouse.gateways.models import Gateway
from ai_greenhouse.gateways.repository import GatewayRepository
from ai_greenhouse.infrastructure.database.base import StatusEnum, utc_now
from ai_greenhouse.points.models import DataQuality, Point, PointDataType
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord, TelemetryWriteOutcome

Clock = Callable[[], datetime]


def canonical_message(message: EdgeTelemetryMessage) -> dict[str, Any]:
    """Return the normalized JSON content compared for idempotency."""
    return message.model_dump(mode="json")


def sample_id_for(gateway_id: UUID, message_id: UUID) -> UUID:
    """Derive a globally unique telemetry sample identity from the public key."""
    return uuid5(gateway_id, str(message_id))


class EdgeService:
    """Validate public identities and delegate facts to the common ingestion path."""

    def __init__(self, session: AsyncSession, *, clock: Clock = utc_now) -> None:
        """Build the service around the request transaction."""
        self._gateways = GatewayRepository(session)
        self._messages = EdgeTelemetryMessageRepository(session)
        self._commands = CommandRepository(session)
        self._ingestion = TelemetryIngestionService(session, clock=clock)
        self._clock = clock

    async def ingest(
        self,
        envelope: EdgeTelemetryEnvelope,
    ) -> EdgeTelemetryIngestionResult:
        """Atomically validate and process one ordered telemetry envelope."""
        gateway = await self._active_gateway(envelope.gateway_id)
        await self._validate_points(gateway, envelope.messages)

        contents: dict[UUID, dict[str, Any]] = {}
        for message in envelope.messages:
            content = canonical_message(message)
            prior = contents.get(message.message_id)
            if prior is not None and prior != content:
                raise telemetry_message_conflict()
            contents[message.message_id] = content

        existing = await self._messages.get_many(
            gateway.id,
            tuple(contents),
        )
        for message_id, stored in existing.items():
            if stored.content != contents[message_id]:
                raise telemetry_message_conflict()

        results: list[EdgeTelemetryResultItem] = []
        for message in envelope.messages:
            content = contents[message.message_id]
            stored = await self._messages.get(gateway.id, message.message_id)
            if stored is not None:
                if stored.content != content:
                    raise telemetry_message_conflict()
                results.append(
                    EdgeTelemetryResultItem(
                        message_id=message.message_id,
                        sample_id=stored.sample_id,
                        outcome=TelemetryWriteOutcome.DUPLICATE,
                        received_at=stored.received_at,
                    )
                )
                continue

            received_at = self._clock()
            sample_id = sample_id_for(gateway.id, message.message_id)
            inserted = await self._messages.insert_if_absent(
                gateway_id=gateway.id,
                message_id=message.message_id,
                sample_id=sample_id,
                content=content,
                received_at=received_at,
            )
            if not inserted:
                stored = await self._messages.get(gateway.id, message.message_id)
                if stored is None or stored.content != content:
                    raise telemetry_message_conflict()
                results.append(
                    EdgeTelemetryResultItem(
                        message_id=message.message_id,
                        sample_id=stored.sample_id,
                        outcome=TelemetryWriteOutcome.DUPLICATE,
                        received_at=stored.received_at,
                    )
                )
                continue

            ingestion = await self._ingestion.ingest(
                TelemetrySampleRecord(
                    id=sample_id,
                    point_id=message.point_id,
                    value=message.value,
                    observed_at=message.observed_at,
                    received_at=received_at,
                    quality=DataQuality(message.quality.value),
                )
            )
            results.append(
                EdgeTelemetryResultItem(
                    message_id=message.message_id,
                    sample_id=sample_id,
                    outcome=ingestion.outcome,
                    received_at=received_at,
                )
            )

        return EdgeTelemetryIngestionResult(
            gateway_id=gateway.id,
            batch_id=envelope.batch_id,
            results=results,
        )

    async def list_commands(self, gateway_id: UUID, *, limit: int) -> EdgeCommandList:
        """Return pending commands visible to one active gateway."""
        gateway = await self._active_gateway(gateway_id)
        commands = await self._commands.list_pending_for_gateway(gateway.id, limit=limit)
        return EdgeCommandList(
            gateway_id=gateway.id,
            commands=[
                EdgePendingCommand(
                    command_id=command.id,
                    point_id=command.target_point_id,
                    reported_point_id=command.reported_point_id,
                    data_type=PointDataType.BOOLEAN,
                    desired_value=command.desired_value,
                    issued_at=command.issued_at,
                )
                for command in commands
            ],
        )

    async def acknowledge(
        self,
        gateway_id: UUID,
        command_id: UUID,
        acknowledgement: EdgeCommandAcknowledgement,
    ) -> EdgeCommandAcknowledgement:
        """Store or replay one terminal command acknowledgement."""
        if gateway_id != acknowledgement.gateway_id or command_id != acknowledgement.command_id:
            raise EdgeValidationError("Path gateway_id and command_id must match the request body.")
        await self._active_gateway(gateway_id)
        command = await self._commands.get_for_gateway_update(gateway_id, command_id)
        if command is None:
            raise command_not_found()

        requested_state = CommandState(acknowledgement.outcome.value)
        requested_reason = (
            None
            if acknowledgement.reason is None
            else acknowledgement.reason.model_dump(mode="json")
        )
        if command.state is CommandState.PENDING:
            command.state = requested_state
            command.acknowledged_at = acknowledgement.acknowledged_at
            command.rejection_reason = requested_reason
            await self._commands.flush()
            return self._stored_acknowledgement(command)

        if command.acknowledged_at is None:
            raise command_not_pending()
        if (
            command.state is requested_state
            and command.acknowledged_at == acknowledgement.acknowledged_at
            and command.rejection_reason == requested_reason
        ):
            return self._stored_acknowledgement(command)
        raise command_acknowledgement_conflict()

    async def _active_gateway(self, gateway_id: UUID) -> Gateway:
        """Resolve an active gateway without writing."""
        gateway = await self._gateways.get_by_id(gateway_id)
        if gateway is None:
            raise gateway_not_found()
        if gateway.status is not StatusEnum.ACTIVE:
            raise gateway_inactive()
        return gateway

    async def _validate_points(
        self,
        gateway: Gateway,
        messages: list[EdgeTelemetryMessage],
    ) -> None:
        """Validate every point relationship before any envelope write."""
        point_ids = tuple(dict.fromkeys(message.point_id for message in messages))
        points = await self._gateways.get_points(point_ids)
        authorized = set(await self._gateways.list_authorized_points(gateway.id))
        for message in messages:
            point: Point | None = points.get(message.point_id)
            if point is None:
                raise point_not_found()
            if point.status is not StatusEnum.ACTIVE:
                raise point_inactive()
            if point.id not in authorized or point.site_id != gateway.site_id:
                raise gateway_point_forbidden()
            if point.data_type is not message.data_type:
                raise EdgeValidationError("Telemetry data_type does not match the logical point.")

    @staticmethod
    def _stored_acknowledgement(command: Command) -> EdgeCommandAcknowledgement:
        """Build the immutable public terminal representation."""
        if command.gateway_id is None or command.acknowledged_at is None:
            raise command_not_pending()
        reason = command.rejection_reason
        return EdgeCommandAcknowledgement.model_validate(
            {
                "contract_version": CONTRACT_VERSION,
                "gateway_id": command.gateway_id,
                "command_id": command.id,
                "outcome": EdgeCommandOutcome(command.state.value),
                "acknowledged_at": command.acknowledged_at,
                **({} if reason is None else {"reason": reason}),
            }
        )
