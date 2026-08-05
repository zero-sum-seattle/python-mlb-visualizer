"""Tests for Alembic migrations against temporary SQLite databases."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.engine import build_engine
from tests.conftest import run_alembic_downgrade_base, run_alembic_upgrade

REVISION_HEAD = "166b6424e4f9"


def database_url_for(path: Path) -> str:
    return f"sqlite:///{path}"


def test_fresh_database_upgrades_to_head(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    run_alembic_upgrade(database_url_for(db_path))
    engine = build_engine(database_url_for(db_path))
    inspector = inspect(engine)
    assert inspector.has_table("team_game_batting_lines")
    engine.dispose()


def test_expected_columns_exist(migrated_db_path: Path) -> None:
    engine = build_engine(database_url_for(migrated_db_path))
    columns = {
        col["name"] for col in inspect(engine).get_columns("team_game_batting_lines")
    }
    engine.dispose()
    assert columns == {
        "id",
        "game_pk",
        "game_date",
        "season",
        "team_id",
        "team_name",
        "opponent_id",
        "opponent_name",
        "home_away",
        "hits",
        "runs",
        "status",
        "game_number",
        "doubleheader",
        "scheduled_innings",
        "created_at",
        "updated_at",
    }


def test_unique_team_id_game_pk_is_enforced(migrated_session: Session) -> None:
    migrated_session.execute(
        text(
            """
            INSERT INTO team_game_batting_lines (
                game_pk, game_date, season, team_id, team_name, opponent_id,
                opponent_name, home_away, hits, runs, status, game_number,
                doubleheader, scheduled_innings, created_at, updated_at
            ) VALUES (
                1, '2025-01-01', 2025, 112, 'A', 134, 'B', 'home',
                1, 1, 'Final', 1, 0, 9, '2025-01-01 00:00:00',
                '2025-01-01 00:00:00'
            )
            """
        )
    )
    migrated_session.commit()
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO team_game_batting_lines (
                    game_pk, game_date, season, team_id, team_name, opponent_id,
                    opponent_name, home_away, hits, runs, status, game_number,
                    doubleheader, scheduled_innings, created_at, updated_at
                ) VALUES (
                    1, '2025-01-02', 2025, 112, 'A', 134, 'B', 'home',
                    2, 2, 'Final', 1, 0, 9, '2025-01-02 00:00:00',
                    '2025-01-02 00:00:00'
                )
                """
            )
        )
        migrated_session.commit()


@pytest.mark.parametrize("column", ["hits", "runs"])
def test_negative_hitting_values_are_rejected(
    migrated_session: Session, column: str
) -> None:
    value = -1
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                f"""
                INSERT INTO team_game_batting_lines (
                    game_pk, game_date, season, team_id, team_name, opponent_id,
                    opponent_name, home_away, hits, runs, status, game_number,
                    doubleheader, scheduled_innings, created_at, updated_at
                ) VALUES (
                    9, '2025-01-01', 2025, 112, 'A', 134, 'B', 'home',
                    {value if column == "hits" else 1},
                    {value if column == "runs" else 1},
                    'Final', 1, 0, 9, '2025-01-01 00:00:00',
                    '2025-01-01 00:00:00'
                )
                """
            )
        )
        migrated_session.commit()


def test_invalid_home_away_is_rejected(migrated_session: Session) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO team_game_batting_lines (
                    game_pk, game_date, season, team_id, team_name, opponent_id,
                    opponent_name, home_away, hits, runs, status, game_number,
                    doubleheader, scheduled_innings, created_at, updated_at
                ) VALUES (
                    10, '2025-01-01', 2025, 112, 'A', 134, 'B', 'invalid',
                    1, 1, 'Final', 1, 0, 9, '2025-01-01 00:00:00',
                    '2025-01-01 00:00:00'
                )
                """
            )
        )
        migrated_session.commit()


def test_invalid_game_number_is_rejected(migrated_session: Session) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO team_game_batting_lines (
                    game_pk, game_date, season, team_id, team_name, opponent_id,
                    opponent_name, home_away, hits, runs, status, game_number,
                    doubleheader, scheduled_innings, created_at, updated_at
                ) VALUES (
                    11, '2025-01-01', 2025, 112, 'A', 134, 'B', 'home',
                    1, 1, 'Final', 0, 0, 9, '2025-01-01 00:00:00',
                    '2025-01-01 00:00:00'
                )
                """
            )
        )
        migrated_session.commit()


def test_invalid_scheduled_innings_is_rejected(migrated_session: Session) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO team_game_batting_lines (
                    game_pk, game_date, season, team_id, team_name, opponent_id,
                    opponent_name, home_away, hits, runs, status, game_number,
                    doubleheader, scheduled_innings, created_at, updated_at
                ) VALUES (
                    12, '2025-01-01', 2025, 112, 'A', 134, 'B', 'home',
                    1, 1, 'Final', 1, 0, 0, '2025-01-01 00:00:00',
                    '2025-01-01 00:00:00'
                )
                """
            )
        )
        migrated_session.commit()


def test_query_index_exists(migrated_db_path: Path) -> None:
    connection = sqlite3.connect(migrated_db_path)
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name="
        "'ix_team_game_batting_lines_team_season_order'"
    ).fetchall()
    connection.close()
    assert rows == [("ix_team_game_batting_lines_team_season_order",)]


def test_downgrade_to_base_removes_table(tmp_path: Path) -> None:
    db_path = tmp_path / "downgrade.db"
    url = database_url_for(db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_base(url)
    engine = build_engine(url)
    assert not inspect(engine).has_table("team_game_batting_lines")
    engine.dispose()


def test_head_revision_identifier() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_current_head() == REVISION_HEAD
