"""Immutable measured values and the single boundary that admits them.

A :class:`~ai_greenhouse.telemetry.models.TelemetrySample` is one value a point
carried at one instant. Samples are append-only: nothing in the application
updates or deletes one, because history that can be rewritten is not history.
The last value of a point stays readable in constant time through
``PointCurrentState``, which this module maintains as a *projection* of the
stream rather than as a second source of truth.

Everything enters through
:meth:`~ai_greenhouse.telemetry.service.TelemetryService.record_sample`. The
simulator calls it in process; a device ingestion adapter will call the same
method later. No other code path — and no database trigger — writes
``point_current_states``, which is what keeps the two properties this module
exists for enforceable in one place:

* **Idempotency.** The sample id is chosen by the producer, so re-delivering a
  sample is a no-op instead of a duplicate row and a spurious revision.
* **Out-of-order tolerance.** A sample that arrives late belongs in history,
  but must never roll the current state back to an older value.

There is no ingestion endpoint. Telemetry arriving over HTTP is a device
integration contract, and it is not designed here on the strength of one
in-process producer.
"""
