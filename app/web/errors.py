"""Human-readable handling of bad query parameters."""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.templating import Jinja2Templates

from app.config import Settings


def describe_validation_errors(error: RequestValidationError) -> list[str]:
    """Render FastAPI validation errors as one sentence per bad parameter."""
    described: list[str] = []
    for detail in error.errors():
        location = detail.get("loc") or ()
        name = str(location[-1]) if location else "request"
        described.append(f"{name}: {detail.get('msg', 'is invalid')}")
    return described


def create_validation_error_handler(
    templates: Jinja2Templates,
    settings: Settings,
) -> Callable[[Request, RequestValidationError], Awaitable[Response]]:
    """Build a handler that answers browsers with a readable page, not a traceback."""

    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> Response:
        if "text/html" not in request.headers.get("accept", ""):
            return await request_validation_exception_handler(request, exc)
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "app_name": settings.app_name,
                "heading": "That link has a value this page cannot use",
                "message": "Adjust the address and try again.",
                "details": describe_validation_errors(exc),
            },
            status_code=422,
        )

    return handle_validation_error
