"""Physical topology of a deployment.

:class:`~ai_greenhouse.topology.models.Site` is the root every facility, zone
and point eventually belongs to. :class:`~ai_greenhouse.topology.models.Facility`
is a growing or infrastructure object inside one site.
:class:`~ai_greenhouse.topology.models.ControlZone` is the part of a facility
that is measured or controlled as one unit.
:class:`~ai_greenhouse.topology.models.ZonePointAssignment` is the link that says
which point takes part in a zone and what part it plays there — and it is what
the composite read of a facility's whole configuration is assembled from.

Control is modelled apart from physical space: ``Area`` and the physical
sub-structure of a facility are out of scope until something declares a consumer
for them, and a zone must not start describing geometry in the meantime.

The assignment is the one place where this module reads the points module. It
reads it to decide which point may take part in which zone; what a point *is*
stays entirely a question for ``ai_greenhouse.points``.
"""
