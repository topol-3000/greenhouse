"""Grow cycles: applying a published recipe version to a real climate zone.

The catalog in :mod:`ai_greenhouse.agronomy` says what an environment should be
and deliberately names no facility, zone, loop or point. This module is the
other half of that separation: it is the only place where generic agronomy and
concrete topology meet, and it holds every operational reference the catalog
refuses.

Activation persists the temperature band a running cycle asks for and stops
there: it reads no telemetry and creates no command. The control module consumes
the active snapshot only when a later accepted current temperature reaches the
existing automation path.
"""
