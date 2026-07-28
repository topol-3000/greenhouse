"""Physical topology of a deployment.

Milestone 1.2 introduced :class:`~ai_greenhouse.topology.models.Site`, the root
every facility, zone and point eventually belongs to. Milestone 1.3 adds
:class:`~ai_greenhouse.topology.models.Facility`, a growing or infrastructure
object inside one site. Milestone 1.4 adds
:class:`~ai_greenhouse.topology.models.ControlZone`, the part of a facility that
is measured or controlled as one unit.

Control is modelled apart from physical space: ``Area`` and the physical
sub-structure of a facility are out of scope until a later milestone declares a
consumer for them, and a zone must not start describing geometry in the
meantime.
"""
