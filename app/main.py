"""FastAPI application factory and ASGI entrypoint."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.web.routes import create_router

TEMPLATES_DIR = Path(__file__).resolve().parent / "web" / "templates"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    application.include_router(create_router(templates, settings))
    return application


app = create_app()
