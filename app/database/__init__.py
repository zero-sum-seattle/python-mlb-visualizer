"""Database package: engine helpers and ORM models."""

from app.database.base import Base
from app.database.engine import build_engine, build_session_factory
from app.database.models import TeamGameBattingLineRecord
from app.database.repositories import (
    DatabaseSchemaMissingError,
    TeamGamePersistenceError,
    list_available_team_seasons,
    list_team_season,
    upsert_team_season,
)

__all__ = [
    "Base",
    "DatabaseSchemaMissingError",
    "TeamGameBattingLineRecord",
    "TeamGamePersistenceError",
    "build_engine",
    "build_session_factory",
    "list_available_team_seasons",
    "list_team_season",
    "upsert_team_season",
]
