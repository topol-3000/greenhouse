# Cloud ↔ Edge contract v1.0

This directory is the canonical, transport-neutral Cloud ↔ Edge contract for
AI Greenhouse. Its JSON Schemas and examples can be copied or consumed without
installing `ai-greenhouse` or importing cloud application code.

Version `1.0` defines:

- telemetry ingestion from one registered gateway;
- pull-based retrieval of pending logical-point commands;
- idempotent command acknowledgement or rejection;
- reported state sent through the same telemetry contract;
- one minimal HTTP/JSON mapping for local integration.

The contract uses only gateway, point, telemetry and command vocabulary.
`quality: "simulated"` is generic provenance metadata and does not introduce a
simulation domain or a dependency on any producer implementation.

## Semantic contract

### Identity and topology

- `gateway_id` is the stable cloud-assigned identity of a gateway. It selects a
  registered integration principal; in v1 it is not an authentication
  credential.
- Every active gateway has an explicit set of authorized logical point IDs.
  Gateway configuration and all authorized points belong to one site.
- `point_id` is the stable cloud logical-point UUID. It is not a device address,
  channel, GPIO, register or producer-local identifier.
- A telemetry write is allowed only when the gateway and point exist, both are
  active, and the point is in that gateway's authorized point set.
- A command is retrievable only by the gateway authorized for its target
  `point_id`. Its `reported_point_id` is also authorized to that gateway and is
  in the same site.
- A gateway naming another gateway's point receives
  `gateway_point_forbidden`. Command lookup does not reveal another gateway's
  commands and answers `command_not_found`.

Gateway persistence and the administrative management-plane API are
deliberately not part of this closed data-plane contract. The implemented
provisioning operations and their stable-code/operational-UUID distinction are
documented in the repository [README](../../../README.md#gateway-management-plane-api).

### Telemetry

`telemetry-envelope.schema.json` carries one to 100 messages from one gateway.
The array is an optional batching boundary, not an offline buffer.

Each message contains:

- a producer-chosen `message_id`;
- a logical `point_id`;
- an explicit `data_type` and matching JSON `value`;
- timezone-aware RFC 3339 `observed_at`;
- quality and source metadata.

`received_at` is prohibited in a request. Cloud assigns it when a new message
is accepted and returns the persisted value in the ingestion result. A replay
returns the original persisted `received_at`, not a new receipt time.

The idempotency key is the tuple `(gateway_id, message_id)`.

- First acceptance records exactly one append-only sample.
- An identical replay returns `duplicate` and creates no sample, state
  revision, state transition, command or automation action.
- Reuse of the key with any different message content returns
  `telemetry_message_conflict`.
- A valid older observation is stored once and returns `out_of_order`; it does
  not replace current state or trigger automation.
- Envelope schema, gateway and point checks are completed before any message is
  written. Failure of one message rejects the whole envelope.
- Messages that pass validation are processed in array order. Results have the
  same order.

`quality: "no_data"` is not a telemetry value. It is reserved for an empty
cloud state projection. The `simulated` member remains a general quality value
and has no special routing or automation behavior.

### Commands

The v1 adapter uses pull retrieval. A returned command contains stable command,
target-point and reported-point identities, an explicit value type, the desired
value and the cloud issue time.

The lifecycle is:

1. Cloud creates a `pending` command.
2. An authorized gateway receives it through command retrieval. Repeated
   retrieval before a terminal acknowledgement is allowed and returns the same
   command identity and content.
3. The gateway applies the desired value locally.
4. After a successful application, the gateway submits two stable telemetry
   messages, normally in one envelope: the commanded value for `point_id` and
   the observed actuator state for `reported_point_id`. The first value equals
   `desired_value`; the second may differ and remains an ordinary telemetry
   fact. Retries reuse both message IDs.
5. After both messages are accepted, the gateway acknowledges with terminal
   outcome `applied`. If it cannot apply the command, it acknowledges with
   terminal outcome `rejected` and a reason; it may still report unchanged
   actuator state through ordinary telemetry.

The acknowledgement itself never writes point state. This preserves the
existing rule that both logical control state and status feedback have one
author: telemetry ingestion.

`applied` and `rejected` are terminal. A command is no longer returned after
either outcome is stored.

An acknowledgement resource is identified by `(gateway_id, command_id)`.
Retrying the exact same acknowledgement, including `acknowledged_at` and
rejection reason, returns the stored terminal representation and changes
nothing. A different acknowledgement for a terminal command returns
`command_acknowledgement_conflict`. A duplicate cannot create another command
transition or reported-state sample.

## Minimal HTTP/JSON adapter

KAN-53 specifies these operations but does not implement them:

| Operation | HTTP mapping | Success |
| --- | --- | --- |
| Ingest telemetry | `POST /api/v1/edge/telemetry` | `200` ingestion result |
| Pull pending commands | `GET /api/v1/edge/gateways/{gateway_id}/commands?limit=100` | `200` command list |
| Acknowledge/reject | `PUT /api/v1/edge/gateways/{gateway_id}/commands/{command_id}/acknowledgement` | `200` stored acknowledgement |

Every request and response uses `Content-Type: application/json`. The
acknowledgement path identities must equal the body identities; disagreement is
`validation_error`. A successful empty command poll returns `commands: []`.

### Errors

Errors use `error.schema.json`. Validation errors may include field-level
details; safe domain errors may omit them.

| HTTP | Code | Meaning and write behavior |
| --- | --- | --- |
| `404` | `gateway_not_found` | Gateway identity is unknown; no write. |
| `409` | `gateway_inactive` | Gateway is archived/inactive; no write. |
| `404` | `point_not_found` | Logical point is unknown; no envelope write. |
| `409` | `point_inactive` | Logical point is archived/inactive; no envelope write. |
| `403` | `gateway_point_forbidden` | Point is not authorized to the gateway or violates its topology; no envelope write. |
| `409` | `telemetry_message_conflict` | A gateway reused a message ID with different content; no envelope write. |
| `404` | `command_not_found` | Command is unknown or is not visible to this gateway; no write. |
| `409` | `command_not_pending` | Command cannot accept a first terminal acknowledgement; no write. |
| `409` | `command_acknowledgement_conflict` | Stored terminal result differs from the retry; no write. |
| `422` | `validation_error` | JSON or schema/path validation failed; no write. |

Authentication failures are not part of v1. Production authentication will add
adapter-level behavior without changing the semantic gateway/point ownership
rules.

## Artifacts and compatibility

`manifest.json` maps each operation to its schema and examples. Schemas use
JSON Schema Draft 2020-12 and closed objects. Request examples under
`examples/valid/` must validate; telemetry examples under `examples/invalid/`
must fail validation.

Compatibility rules:

- the version is the exact string `1.0`;
- published v1.0 schemas are immutable;
- patch releases may clarify prose or add examples without changing validation;
- a minor version may add a new optional operation or schema, but cannot change
  an existing closed object;
- changing an existing field, enum, required set, identity or idempotency rule
  requires a new major version;
- producers and consumers must reject an unsupported contract version.

Independent consumers can validate these artifacts with any Draft 2020-12
validator. The repository confidence check is:

```bash
uv run pytest tests/contracts
```

## Explicitly deferred

This contract does not choose or introduce MQTT, a broker abstraction,
production authentication, production gateway provisioning, durable offline
buffering, delivery leases, push delivery, OTA, device discovery, physical
addresses, autonomous Edge rules, or a universal device/controller framework.
