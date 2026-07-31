"""Delivery of the one same-origin dashboard page.

The page is served by this application rather than by a second container, so the
browser reads the domain API under ``/api/v1`` as a same-origin request and no
CORS configuration exists to get wrong.

Each asset has its own route instead of a mounted static directory. There are
exactly three files, the page names them itself, and a wildcard path that maps
request text onto the filesystem is a larger surface than three constants.

The dashboard reads the API and never writes through this module: there is no
template, no server-side rendering and no aggregate endpoint here.
"""

from pathlib import Path
from typing import Final

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC_DIRECTORY: Final[Path] = Path(__file__).resolve().parents[1] / "static"
"""Directory holding the page and its two assets."""

INDEX_FILE: Final[str] = "index.html"
STYLESHEET_FILE: Final[str] = "dashboard.css"
SCRIPT_FILE: Final[str] = "dashboard.js"

STYLESHEET_PATH: Final[str] = f"/static/{STYLESHEET_FILE}"
SCRIPT_PATH: Final[str] = f"/static/{SCRIPT_FILE}"
"""The two asset URLs the page references, named here so a test can assert them."""

router: APIRouter = APIRouter(tags=["dashboard"])


@router.get("/", include_in_schema=False)
async def get_dashboard() -> FileResponse:
    """Serve the facility dashboard page.

    Returns:
        The single HTML document. It carries no data: the page discovers the
        configured facility through the same public endpoints any other client
        would use, and shows an explicit empty state while there is none.
    """
    return FileResponse(STATIC_DIRECTORY / INDEX_FILE, media_type="text/html")


@router.get(STYLESHEET_PATH, include_in_schema=False)
async def get_dashboard_stylesheet() -> FileResponse:
    """Serve the dashboard stylesheet.

    Returns:
        The stylesheet the page references.
    """
    return FileResponse(STATIC_DIRECTORY / STYLESHEET_FILE, media_type="text/css")


@router.get(SCRIPT_PATH, include_in_schema=False)
async def get_dashboard_script() -> FileResponse:
    """Serve the dashboard script.

    Returns:
        The script the page references.
    """
    return FileResponse(STATIC_DIRECTORY / SCRIPT_FILE, media_type="text/javascript")
