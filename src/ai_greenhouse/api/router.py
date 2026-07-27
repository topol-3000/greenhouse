"""Top-level routers mounted by ``create_app()``.

``root_router`` carries unversioned endpoints such as ``GET /health``.
``api_v1_router`` is the versioned router that domain-entity stories mount
their endpoints on.
"""

from fastapi import APIRouter

from ai_greenhouse.api.routes.health import router as health_router

API_V1_PREFIX: str = "/api/v1"

root_router: APIRouter = APIRouter()
root_router.include_router(health_router)

api_v1_router: APIRouter = APIRouter(prefix=API_V1_PREFIX)
"""Versioned router for domain endpoints.

Empty until the entity stories mount their routers here. ``GET /health`` stays
on ``root_router`` and is not duplicated under the prefix.
"""
