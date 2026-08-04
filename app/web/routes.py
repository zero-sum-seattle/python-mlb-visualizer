"""HTTP routes for the web UI and health check."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings


def create_router(templates: Jinja2Templates, settings: Settings) -> APIRouter:
    """Build the application router with template and settings dependencies."""
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        """Render the foundation landing page."""
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "app_name": settings.app_name,
                "environment": settings.environment,
            },
        )

    @router.get("/health", response_class=JSONResponse)
    async def health() -> dict[str, str]:
        """Return a simple health-check payload."""
        return {
            "status": "ok",
            "app": settings.app_name,
        }

    return router
