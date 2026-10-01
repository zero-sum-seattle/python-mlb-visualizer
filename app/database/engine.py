"""Database engine and session factory construction."""

import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry


def build_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for the given database URL.

    SQLite engines enable foreign-key enforcement on every new connection.
    """
    is_sqlite = make_url(database_url).get_backend_name() == "sqlite"
    connect_args: dict[str, object] = {}
    if is_sqlite:
        connect_args["check_same_thread"] = False
    engine = create_engine(database_url, connect_args=connect_args)
    if is_sqlite:
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


class SQLiteForeignKeyEnforcementError(RuntimeError):
    """A SQLite connection could not be configured to enforce foreign keys."""


def _enable_sqlite_foreign_keys(
    dbapi_connection: DBAPIConnection,
    connection_record: ConnectionPoolEntry,
) -> None:
    """Turn on enforcement of the schema's declared foreign keys.

    SQLite parses ``FOREIGN KEY`` clauses but ignores them unless each
    connection sets ``PRAGMA foreign_keys = ON``, and silently ignores that
    pragma inside an open transaction. Following SQLAlchemy's SQLite dialect
    guidance, the pragma runs with ``sqlite3`` autocommit temporarily on, so
    it never depends on the driver's transaction-control mode. The setting is
    read back so a connection that cannot enforce foreign keys fails loudly.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        raise SQLiteForeignKeyEnforcementError(
            "Expected a sqlite3 connection for a SQLite database URL, got "
            f"{type(dbapi_connection).__name__}"
        )

    previous_autocommit = dbapi_connection.autocommit
    dbapi_connection.autocommit = True
    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            enabled = cursor.execute("PRAGMA foreign_keys").fetchone()
        finally:
            cursor.close()
    finally:
        dbapi_connection.autocommit = previous_autocommit

    if enabled != (1,):
        raise SQLiteForeignKeyEnforcementError(
            "SQLite did not enable foreign-key enforcement "
            f"(PRAGMA foreign_keys returned {enabled!r})"
        )
