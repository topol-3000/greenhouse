# ADR 0002: The first-harvest automation boundary

- Status: Accepted
- Date: 2026-08-03
- Supersedes: [ADR 0001](0001-recipe-runtime-target-control-boundary.md)

## Context

Milestone 5 delivered an agronomy catalog, a grow cycle lifecycle and a
`RuntimeTarget` that fed the `hysteresis-v1` control loop, and
[ADR 0001](0001-recipe-runtime-target-control-boundary.md) recorded the
precedence rule that made it executable. The product direction changed before
any of it was used to grow anything: the next step is a **basil growing journal
with sensor monitoring**, and the only automated function planned for the first
harvest is a **lighting photoperiod**.

That leaves the M5 surface with no consumer. It is a second executable source of
a temperature band, an agronomy catalog nothing reads and a cycle lifecycle
nothing advances — all of it addressing a fan, which the first harvest does not
automate at all. Carrying it forward would mean maintaining a precedence rule,
an immutability invariant, a partial unique index and a provenance column
through a milestone that exercises none of them.

There were two ways to leave it behind: freeze it in place unused, or remove it
from the active product. The frozen option costs a migration, an ORM model, a
router, a read model and a set of invariants in every future change that touches
`control`, and it advertises a capability in the OpenAPI document that the
product is not committing to.

## Decision

- Milestone 5 is removed from the active product: from runtime code, the latest
  schema, the public contracts, the dashboard, active documentation and the
  tests. Published history — every migration file, commit and pull request —
  stays exactly as it is.
- `hysteresis-v1` resolves its band from `ControlLoop.lower_threshold` and
  `ControlLoop.upper_threshold` and from nothing else. There is one source, so
  a `Command` records no source: `commands.runtime_target_id` is dropped.
- The removal is a **forward compensating migration**, `20260803_0019`.
  `20260801_0016`, `20260801_0017` and `20260801_0018` still run and are not
  edited. `20260803_0019` drops `commands.runtime_target_id` before
  `runtime_targets`, and the grow-lifecycle tables before the catalog they
  reference. Its `downgrade` rebuilds the schema and recovers no row: the data
  deletion is irreversible and a restore is a restore from backup.
- The generic `ControlLoop` and `Command` infrastructure stays. It is not
  advertised for the first harvest — no temperature or fan automation is
  configured or documented as part of it — but the mechanism is the one a
  lighting photoperiod would be built on and is not worth rebuilding.
- Everything M0–M4 keeps its behaviour: the topology, append-only telemetry and
  the current-state projection, command idempotency and delivery, gateway
  configuration and point authorization, Cloud ↔ Edge v1, clean startup with no
  seed data, and the dashboard as a read model of the public API.

## Consequences

The cloud has one automation rule with one source of its band, which is the
boundary it had before Milestone 5 and the smallest thing the first harvest can
be built on top of. Nothing that reached a gateway changes: the Cloud ↔ Edge v1
envelope never carried a runtime target, so an Edge client sees no difference at
all.

An installation that ran a grow cycle loses its cycles, its recipes and its
runtime targets when it migrates. Its telemetry, its current state and its
commands are untouched, and a command that was decided from a target keeps
everything about that decision except the name of the target.

Recipe-driven automation, humidity, irrigation, nutrients, pH/EC, adaptive
brightness and adaptive photoperiod remain out of scope. The journal entities,
photos, event timeline, charts, lighting schedule and manual override that the
first harvest calls for are **not** implemented by this decision either; it
clears the ground for them and does no more than that.
