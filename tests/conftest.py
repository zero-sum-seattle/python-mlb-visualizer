"""Shared pytest fixtures for database-backed tests."""

from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.database.engine import build_engine, build_session_factory


def run_alembic_upgrade(database_url: str) -> None:
    """Apply all Alembic migrations to the database at ``database_url``."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


def run_alembic_downgrade_base(database_url: str) -> None:
    """Remove all Alembic migrations from the database at ``database_url``."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(cfg, "base")


@pytest.fixture
def migrated_db_path(tmp_path: Path) -> Path:
    """File-backed SQLite database with schema at Alembic head."""
    db_path = tmp_path / "test.db"
    database_url = f"sqlite:///{db_path}"
    run_alembic_upgrade(database_url)
    return db_path


@pytest.fixture
def migrated_session(migrated_db_path: Path) -> Generator[Session, None, None]:
    """SQLAlchemy session bound to a migrated temporary database."""
    database_url = f"sqlite:///{migrated_db_path}"
    engine = build_engine(database_url)
    session_factory = build_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
