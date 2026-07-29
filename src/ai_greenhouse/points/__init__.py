"""Logical points and their last known state.

A :class:`~ai_greenhouse.points.models.Point` is the stable logical identity of
a measured, controlled or reported value — air temperature, fan power, pump
running. It carries the *meaning* of the value and deliberately nothing about
where that value physically comes from: no device, no channel, no GPIO pin, no
Modbus register. Those belong to a future ``PointBinding``, and replacing a
sensor there must leave the point identity, its history and every rule referring
to it untouched. That separation is the reason this module exists apart from
``topology``.

:class:`~ai_greenhouse.points.models.PointCurrentState` is the fast-read
projection of the last known value, created together with its point. It starts
empty — ``value`` is ``NULL`` and ``quality`` is ``no_data`` — and is filled by
the telemetry write boundary. It is a projection, not the source of truth: the
historical truth is the telemetry stream it can be rebuilt from.
"""
