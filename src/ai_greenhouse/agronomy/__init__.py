"""The cloud-owned agronomy catalog.

A crop is what is grown. A growing recipe is the stable identity of one way of
growing it, and a recipe version is one immutable, published statement of the
environment that way of growing asks for.

Recipes describe *environmental requirements only*. Nothing here names a
facility, a zone, a control loop, a gateway, a point, a device or an actuator:
that separation is what lets one recipe be applied to any growbox, and it is
also why this module never imports the topology, control or edge modules.
"""
