"""FastAPI application factory and ASGI entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.database.engine import build_engine, build_session_factory
from app.web.errors import create_validation_error_handler
from app.web.routes import create_router

WEB_DIR = Path(__file__).resolve().parent / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Own the database engine for the life of the application.

    The engine is built here rather than at import time so tests and scripts
    can target their own database. Alembic, not startup, creates the schema.
    """
    engine = build_engine(get_settings().database_url)
    application.state.db_engine = engine
    application.state.session_factory = build_session_factory(engine)
    try:
        yield
    finally:
        application.state.session_factory = None
        application.state.db_engine = None
        engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.add_exception_handler(
        RequestValidationError,
        create_validation_error_handler(templates, settings),
    )
    application.include_router(create_router(templates, settings))
    return application


app = create_app()
