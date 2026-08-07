"""FastAPI dependencies for the web layer."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker


class DatabaseNotConfiguredError(RuntimeError):
    """A request needed the database before the application lifespan ran."""


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """Return the session factory the application lifespan installed."""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise DatabaseNotConfiguredError(
            "No database session factory on app.state; the application "
            "lifespan did not run"
        )
    return session_factory


def get_db_session(request: Request) -> Iterator[Session]:
    """Yield a read-only-by-convention session for the duration of a request.

    Tests override this dependency to point at an isolated temporary database.
    """
    session = get_session_factory(request)()
    try:
        yield session
    finally:
        session.close()
