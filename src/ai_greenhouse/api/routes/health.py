from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ai_greenhouse.api.dependencies import (
    DatabaseHealthProbe,
    get_app_settings,
    get_database_health_probe,
)
from ai_greenhouse.core.config import Settings

router = APIRouter(tags=["health"])
logger = structlog.get_logger(__name__)


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "unavailable"]
    service: str
    database: Literal["ok", "unavailable"]


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def health(
    probe: Annotated[DatabaseHealthProbe, Depends(get_database_health_probe)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> HealthResponse | JSONResponse:
    try:
        await probe()
    except Exception:
        logger.exception("database_health_check_failed")
        response = HealthResponse(
            status="unavailable",
            service=settings.app_name,
            database="unavailable",
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(),
        )

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        database="ok",
    )
