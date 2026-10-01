"""Database engine and session factory construction."""

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


def _enable_sqlite_foreign_keys(
    dbapi_connection: DBAPIConnection,
    connection_record: ConnectionPoolEntry,
) -> None:
    """Turn on enforcement of the schema's declared foreign keys.

    SQLite parses ``FOREIGN KEY`` clauses but ignores them unless each
    connection sets this pragma. The pragma is a no-op inside an open
    transaction; it takes effect here because the driver's default (legacy)
    transaction control has not begun one when a connection is first opened.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()
