# ADR 0001: Recipe to RuntimeTarget control boundary

- Status: Superseded by
  [ADR 0002](0002-first-harvest-automation-boundary.md)
- Date: 2026-08-01
- Superseded: 2026-08-03

## What this record is now

This ADR decided that an active `RuntimeTarget` snapshotted from a Growing
Recipe took precedence over a `ControlLoop`'s own thresholds, that a
target-derived `Command` recorded `runtime_target_id`, and that the loop's
thresholds stayed as a legacy fallback.

**None of that is implemented.** Milestone 5 was rolled back out of the active
product on 2026-08-03: `Crop`, `GrowingRecipe`, `RecipeVersion`, `RecipeStage`,
`TargetRequirement`, `GrowCycle`, `GrowCycleZoneAssignment`,
`GrowStageInstance` and `RuntimeTarget` no longer exist in the runtime, the
latest schema, the public API or the dashboard, and `commands` no longer carries
a `runtime_target_id` column. `hysteresis-v1` decides on
`ControlLoop.lower_threshold` and `ControlLoop.upper_threshold` and on nothing
else — which is the boundary this ADR replaced and the boundary that is back.

The decision this record described is kept here rather than deleted, because
commit `e4a6a0d` and pull request #38 implemented it and the schema history in
`migrations/versions/` still applies and un-applies it. What it does not
describe is the current system. Read
[ADR 0002](0002-first-harvest-automation-boundary.md) for that.

## Why it was reverted

The precedence rule was correct for the milestone it belonged to and wrong for
the product that follows it. The next step is a basil growing journal with
sensor monitoring, whose only planned automated function is a lighting
photoperiod. A recipe-driven temperature target executed by a fan is neither
part of that step nor a prerequisite for it, and keeping it would have meant
carrying an agronomy catalog, a cycle lifecycle and a second executable source
of the same band through a milestone that uses none of them.

The rollback removes scope. It does not overturn the reasoning: if
recipe-driven targets return, this record is the argument they would start from
and ADR 0002 states what they would have to replace.
