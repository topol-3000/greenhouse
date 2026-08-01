# ADR 0001: Recipe to RuntimeTarget control boundary

- Status: Accepted
- Date: 2026-08-01

## Context

A Growing Recipe defines the environmental conditions a plant requires.
`hysteresis-v1` defines how the system reacts to temperature. Before Milestone
5, immutable thresholds on `ControlLoop` were the only executable source.
Milestone 5 Unit 2 introduced `RuntimeTarget` as an immutable snapshot of the
active GrowCycle's temperature requirement, but automation did not consume it.

The control decision must use one coherent pair of bounds and retain enough
provenance to show which persisted configuration produced a command. Existing
installations without active GrowCycles must keep working, and the Edge command
contract must remain independent of cloud agronomy.

## Decision

- An active `RuntimeTarget` for the evaluated `ControlLoop`, identified by
  `control_loop_id` and `effective_to IS NULL`, takes precedence.
- When no active target exists, the loop's immutable legacy thresholds remain
  the compatibility fallback.
- Target selection uses transaction-time state. A telemetry sample's
  `observed_at` does not select historical targets.
- One resolver returns the effective lower bound, upper bound, source type and
  nullable RuntimeTarget ID together. Evaluation does not query bounds and
  provenance separately.
- The selected active target is held through command persistence. Invalid
  active target data fails automation safely and is never treated as absence.
- A target-derived `Command` persists `runtime_target_id`; a legacy-derived
  command persists null.
- The existing telemetry ingestion, hysteresis, command idempotency, delivery
  and acknowledgement path remains the only automation path.
- The Cloud ↔ Edge v1 command envelope remains agronomy-agnostic and carries no
  RuntimeTarget field.

## Consequences

The cloud can prove which RuntimeTarget snapshot produced an executed
temperature decision, including after its GrowCycle closes. Existing
installations without an active target retain their current behavior. Legacy
thresholds remain duplicated as a temporary compatibility fallback; removing
them is deferred.

This decision does not introduce a generic policy engine. Multiple policies,
safety constraints, manual overrides, and shared-zone target merging remain
future decisions.
