"""The lifecycle rule every domain resource obeys, asserted in one place.

Entities are retired through their lifecycle and never removed.
The rule was written out once per entity module before, which meant four copies
of the same request and no test at all for a resource someone forgot. Driving
every entity from one list makes the omission the failing case.
"""

from uuid import uuid4

import httpx

from tests.integration.factories import (
    CONTROL_LOOPS_URL,
    CONTROL_ZONES_URL,
    CROPS_URL,
    FACILITIES_URL,
    GROWING_RECIPES_URL,
    POINTS_URL,
    RECIPE_VERSIONS_URL,
    SITES_URL,
    create_crop,
    create_growbox,
    create_point,
    create_recipe,
)


async def test_no_domain_entity_can_be_deleted(http_client: httpx.AsyncClient) -> None:
    """``DELETE`` exists only for a zone-point assignment, which is a link, not an entity."""
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    crop = await create_crop(http_client)
    recipe = await create_recipe(http_client, crop["id"])
    entity_urls: dict[str, str] = {
        "site": f"{SITES_URL}/{site['id']}",
        "facility": f"{FACILITIES_URL}/{facility['id']}",
        "control zone": f"{CONTROL_ZONES_URL}/{control_zone['id']}",
        "point": f"{POINTS_URL}/{point['id']}",
        "control loop": f"{CONTROL_LOOPS_URL}/{uuid4()}",
        "crop": f"{CROPS_URL}/{crop['id']}",
        "growing recipe": f"{GROWING_RECIPES_URL}/{recipe['id']}",
        "recipe version": f"{RECIPE_VERSIONS_URL}/{recipe['version']['id']}",
    }

    for entity, url in entity_urls.items():
        response = await http_client.delete(url)
        assert response.status_code == 405, f"archiving is the only way to retire a {entity}"
