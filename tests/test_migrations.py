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

REVISION_HEAD = "2efdbec9b07e"


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
        "strikeouts",
        "base_on_balls",
        "hit_by_pitch",
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


def test_downgrade_to_base_removes_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "downgrade.db"
    url = database_url_for(db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_base(url)
    engine = build_engine(url)
    inspector = inspect(engine)
    assert not inspector.has_table("team_game_batting_lines")
    assert not inspector.has_table("league_season_ingestions")
    engine.dispose()


def test_head_revision_identifier() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_current_head() == REVISION_HEAD


PRE_STRIKEOUTS_REVISION = "166b6424e4f9"
STRIKEOUTS_REVISION = "94dec6973c80"

LEGACY_INSERT = """
    INSERT INTO team_game_batting_lines (
        game_pk, game_date, season, team_id, team_name, opponent_id,
        opponent_name, home_away, hits, runs, status, game_number,
        doubleheader, scheduled_innings, created_at, updated_at
    ) VALUES
        (776704, '2025-08-17', 2025, 136, 'Seattle Mariners', 142,
         'Minnesota Twins', 'home', 6, 4, 'Final', 1, 0, 9,
         '2025-08-17 00:00:00', '2025-08-17 00:00:00'),
        (776705, '2025-08-18', 2025, 136, 'Seattle Mariners', 142,
         'Minnesota Twins', 'away', 11, 7, 'Final', 1, 0, 9,
         '2025-08-18 00:00:00', '2025-08-18 00:00:00')
"""


def run_alembic_upgrade_to(database_url: str, revision: str) -> None:
    """Apply migrations up to one specific revision."""
    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, revision)


def run_alembic_downgrade_to(database_url: str, revision: str) -> None:
    """Roll migrations back to one specific revision."""
    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(cfg, revision)


@pytest.fixture
def legacy_db_path(tmp_path: Path) -> Path:
    """Database at the pre-3.5 revision holding two valid rows without strikeouts."""
    db_path = tmp_path / "legacy.db"
    run_alembic_upgrade_to(database_url_for(db_path), PRE_STRIKEOUTS_REVISION)
    connection = sqlite3.connect(db_path)
    connection.execute(LEGACY_INSERT)
    connection.commit()
    connection.close()
    return db_path


def test_pre_strikeouts_revision_has_no_strikeouts_column(legacy_db_path: Path) -> None:
    engine = build_engine(database_url_for(legacy_db_path))
    columns = {
        col["name"] for col in inspect(engine).get_columns("team_game_batting_lines")
    }
    engine.dispose()
    assert "strikeouts" not in columns


def test_upgrade_preserves_existing_rows(legacy_db_path: Path) -> None:
    run_alembic_upgrade(database_url_for(legacy_db_path))
    connection = sqlite3.connect(legacy_db_path)
    rows = connection.execute(
        "SELECT game_pk, team_name, opponent_name, home_away, hits, runs, "
        "status, scheduled_innings FROM team_game_batting_lines ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert rows == [
        (776704, "Seattle Mariners", "Minnesota Twins", "home", 6, 4, "Final", 9),
        (776705, "Seattle Mariners", "Minnesota Twins", "away", 11, 7, "Final", 9),
    ]


def test_upgrade_leaves_existing_strikeouts_null(legacy_db_path: Path) -> None:
    """Unknown history stays unknown; a zero default would invent a statistic."""
    run_alembic_upgrade(database_url_for(legacy_db_path))
    connection = sqlite3.connect(legacy_db_path)
    values = connection.execute(
        "SELECT strikeouts FROM team_game_batting_lines ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert values == [(None,), (None,)]


def test_upgraded_legacy_rows_still_read_as_domain_records(
    legacy_db_path: Path,
) -> None:
    """The hits page reads these rows through the same repository call."""
    from app.database.engine import build_session_factory
    from app.database.repositories import list_team_season

    run_alembic_upgrade(database_url_for(legacy_db_path))
    engine = build_engine(database_url_for(legacy_db_path))
    session = build_session_factory(engine)()
    try:
        stored = list_team_season(session, team_id=136, season=2025)
    finally:
        session.close()
        engine.dispose()

    assert [(line.game_pk, line.hits, line.strikeouts) for line in stored] == [
        (776704, 6, None),
        (776705, 11, None),
    ]


def test_upgrade_keeps_the_query_index(legacy_db_path: Path) -> None:
    """The table is rebuilt in batch mode, so the index must be re-created."""
    run_alembic_upgrade(database_url_for(legacy_db_path))
    connection = sqlite3.connect(legacy_db_path)
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name="
        "'ix_team_game_batting_lines_team_season_order'"
    ).fetchall()
    connection.close()
    assert rows == [("ix_team_game_batting_lines_team_season_order",)]


@pytest.mark.parametrize("column", ["hits", "runs"])
def test_upgrade_keeps_the_existing_check_constraints(
    legacy_db_path: Path, column: str
) -> None:
    """A batch rebuild driven by reflection alone would have dropped these."""
    run_alembic_upgrade(database_url_for(legacy_db_path))
    connection = sqlite3.connect(legacy_db_path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"""
            INSERT INTO team_game_batting_lines (
                game_pk, game_date, season, team_id, team_name, opponent_id,
                opponent_name, home_away, hits, runs, status, game_number,
                doubleheader, scheduled_innings, created_at, updated_at
            ) VALUES (
                800, '2025-01-01', 2025, 136, 'A', 142, 'B', 'home',
                {-1 if column == "hits" else 1},
                {-1 if column == "runs" else 1},
                'Final', 1, 0, 9, '2025-01-01 00:00:00', '2025-01-01 00:00:00'
            )
            """
        )
    connection.close()


def test_negative_strikeouts_are_rejected(migrated_session: Session) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO team_game_batting_lines (
                    game_pk, game_date, season, team_id, team_name, opponent_id,
                    opponent_name, home_away, hits, runs, status, game_number,
                    doubleheader, scheduled_innings, created_at, updated_at,
                    strikeouts
                ) VALUES (
                    20, '2025-01-01', 2025, 112, 'A', 134, 'B', 'home',
                    1, 1, 'Final', 1, 0, 9, '2025-01-01 00:00:00',
                    '2025-01-01 00:00:00', -1
                )
                """
            )
        )
        migrated_session.commit()


@pytest.mark.parametrize("value", ["NULL", "0", "14"])
def test_null_and_nonnegative_strikeouts_are_accepted(
    migrated_session: Session, value: str
) -> None:
    migrated_session.execute(
        text(
            f"""
            INSERT INTO team_game_batting_lines (
                game_pk, game_date, season, team_id, team_name, opponent_id,
                opponent_name, home_away, hits, runs, status, game_number,
                doubleheader, scheduled_innings, created_at, updated_at,
                strikeouts
            ) VALUES (
                {21 + len(value)}, '2025-01-01', 2025, 112, 'A', 134, 'B', 'home',
                1, 1, 'Final', 1, 0, 9, '2025-01-01 00:00:00',
                '2025-01-01 00:00:00', {value}
            )
            """
        )
    )
    migrated_session.commit()


def test_downgrade_removes_the_strikeouts_column(legacy_db_path: Path) -> None:
    url = database_url_for(legacy_db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_to(url, PRE_STRIKEOUTS_REVISION)
    engine = build_engine(url)
    columns = {
        col["name"] for col in inspect(engine).get_columns("team_game_batting_lines")
    }
    engine.dispose()
    assert "strikeouts" not in columns


def test_downgrade_keeps_the_other_columns_and_rows(legacy_db_path: Path) -> None:
    url = database_url_for(legacy_db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_to(url, PRE_STRIKEOUTS_REVISION)
    connection = sqlite3.connect(legacy_db_path)
    rows = connection.execute(
        "SELECT game_pk, hits, runs FROM team_game_batting_lines ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert rows == [(776704, 6, 4), (776705, 11, 7)]


def test_upgrade_downgrade_upgrade_round_trips(legacy_db_path: Path) -> None:
    url = database_url_for(legacy_db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_to(url, PRE_STRIKEOUTS_REVISION)
    run_alembic_upgrade(url)
    connection = sqlite3.connect(legacy_db_path)
    rows = connection.execute(
        "SELECT game_pk, strikeouts FROM team_game_batting_lines ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert rows == [(776704, None), (776705, None)]


def test_strikeouts_revision_follows_the_creation_revision() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision(STRIKEOUTS_REVISION)
    assert revision.down_revision == PRE_STRIKEOUTS_REVISION


# ---------------------------------------------------------------------------
# Milestone 4: league-season ingestion coverage table
# ---------------------------------------------------------------------------

PRE_LEAGUE_REVISION = "94dec6973c80"
LEAGUE_REVISION = "7f2c4b8e91d3"

# Two rows with a real batting strikeout total, as Milestone 3.5 ingestion
# writes them, plus one row left over from before that column existed.
PRE_M4_INSERT = """
    INSERT INTO team_game_batting_lines (
        game_pk, game_date, season, team_id, team_name, opponent_id,
        opponent_name, home_away, hits, runs, strikeouts, status, game_number,
        doubleheader, scheduled_innings, created_at, updated_at
    ) VALUES
        (776704, '2025-08-17', 2025, 136, 'Seattle Mariners', 142,
         'Minnesota Twins', 'home', 6, 4, 9, 'Final', 1, 0, 9,
         '2025-08-17 00:00:00', '2025-08-17 00:00:00'),
        (776705, '2025-08-18', 2025, 136, 'Seattle Mariners', 142,
         'Minnesota Twins', 'away', 11, 7, 12, 'Final', 1, 0, 9,
         '2025-08-18 00:00:00', '2025-08-18 00:00:00'),
        (776706, '2025-08-19', 2025, 136, 'Seattle Mariners', 142,
         'Minnesota Twins', 'home', 8, 2, NULL, 'Final', 1, 0, 9,
         '2025-08-19 00:00:00', '2025-08-19 00:00:00')
"""


@pytest.fixture
def pre_m4_db_path(tmp_path: Path) -> Path:
    """Database at the Milestone 3.5 revision holding real game rows."""
    db_path = tmp_path / "pre_m4.db"
    run_alembic_upgrade_to(database_url_for(db_path), PRE_LEAGUE_REVISION)
    connection = sqlite3.connect(db_path)
    connection.execute(PRE_M4_INSERT)
    connection.commit()
    connection.close()
    return db_path


def test_pre_m4_revision_has_no_league_table(pre_m4_db_path: Path) -> None:
    engine = build_engine(database_url_for(pre_m4_db_path))
    assert not inspect(engine).has_table("league_season_ingestions")
    engine.dispose()


def test_m4_upgrade_creates_the_league_table(pre_m4_db_path: Path) -> None:
    run_alembic_upgrade(database_url_for(pre_m4_db_path))
    engine = build_engine(database_url_for(pre_m4_db_path))
    columns = {
        col["name"] for col in inspect(engine).get_columns("league_season_ingestions")
    }
    engine.dispose()
    assert columns == {
        "id",
        "season",
        "status",
        "expected_team_count",
        "successful_team_count",
        "failed_team_count",
        "started_at",
        "completed_at",
    }


def test_m4_upgrade_preserves_existing_game_rows(pre_m4_db_path: Path) -> None:
    run_alembic_upgrade(database_url_for(pre_m4_db_path))
    connection = sqlite3.connect(pre_m4_db_path)
    rows = connection.execute(
        "SELECT game_pk, team_name, hits, runs, strikeouts, status "
        "FROM team_game_batting_lines ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert rows == [
        (776704, "Seattle Mariners", 6, 4, 9, "Final"),
        (776705, "Seattle Mariners", 11, 7, 12, "Final"),
        (776706, "Seattle Mariners", 8, 2, None, "Final"),
    ]


def test_m4_upgrade_preserves_the_strikeouts_column_semantics(
    pre_m4_db_path: Path,
) -> None:
    """Milestone 3.5 history survives: known values kept, unknown still NULL."""
    from app.database.engine import build_session_factory
    from app.database.repositories import list_team_season

    run_alembic_upgrade(database_url_for(pre_m4_db_path))
    engine = build_engine(database_url_for(pre_m4_db_path))
    session = build_session_factory(engine)()
    try:
        stored = list_team_season(session, team_id=136, season=2025)
    finally:
        session.close()
        engine.dispose()
    assert [line.strikeouts for line in stored] == [9, 12, None]


def test_m4_upgrade_keeps_the_game_query_index(pre_m4_db_path: Path) -> None:
    run_alembic_upgrade(database_url_for(pre_m4_db_path))
    connection = sqlite3.connect(pre_m4_db_path)
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name="
        "'ix_team_game_batting_lines_team_season_order'"
    ).fetchall()
    connection.close()
    assert rows == [("ix_team_game_batting_lines_team_season_order",)]


@pytest.mark.parametrize("column", ["hits", "runs", "strikeouts"])
def test_m4_upgrade_keeps_the_game_check_constraints(
    pre_m4_db_path: Path, column: str
) -> None:
    run_alembic_upgrade(database_url_for(pre_m4_db_path))
    connection = sqlite3.connect(pre_m4_db_path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"""
            INSERT INTO team_game_batting_lines (
                game_pk, game_date, season, team_id, team_name, opponent_id,
                opponent_name, home_away, hits, runs, strikeouts, status,
                game_number, doubleheader, scheduled_innings, created_at,
                updated_at
            ) VALUES (
                900, '2025-01-01', 2025, 136, 'A', 142, 'B', 'home',
                {-1 if column == "hits" else 1},
                {-1 if column == "runs" else 1},
                {-1 if column == "strikeouts" else 1},
                'Final', 1, 0, 9, '2025-01-01 00:00:00', '2025-01-01 00:00:00'
            )
            """
        )
    connection.close()


def test_m4_upgrade_enforces_the_coverage_constraints(pre_m4_db_path: Path) -> None:
    """A partial run cannot be recorded as complete at the database level."""
    run_alembic_upgrade(database_url_for(pre_m4_db_path))
    connection = sqlite3.connect(pre_m4_db_path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO league_season_ingestions (
                season, status, expected_team_count, successful_team_count,
                failed_team_count, started_at, completed_at
            ) VALUES (
                2025, 'COMPLETE', 30, 29, 1, '2026-03-01 12:00:00',
                '2026-03-01 12:05:00'
            )
            """
        )
    connection.close()


def test_m4_upgrade_enforces_one_row_per_season(pre_m4_db_path: Path) -> None:
    run_alembic_upgrade(database_url_for(pre_m4_db_path))
    connection = sqlite3.connect(pre_m4_db_path)
    connection.execute(
        """
        INSERT INTO league_season_ingestions (
            season, status, expected_team_count, successful_team_count,
            failed_team_count, started_at, completed_at
        ) VALUES (2025, 'RUNNING', 30, 0, 0, '2026-03-01 12:00:00', NULL)
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO league_season_ingestions (
                season, status, expected_team_count, successful_team_count,
                failed_team_count, started_at, completed_at
            ) VALUES (2025, 'RUNNING', 30, 0, 0, '2026-03-02 12:00:00', NULL)
            """
        )
    connection.close()


def test_m4_downgrade_removes_only_the_league_table(pre_m4_db_path: Path) -> None:
    url = database_url_for(pre_m4_db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_to(url, PRE_LEAGUE_REVISION)
    engine = build_engine(url)
    inspector = inspect(engine)
    assert not inspector.has_table("league_season_ingestions")
    assert inspector.has_table("team_game_batting_lines")
    engine.dispose()


def test_m4_downgrade_keeps_every_game_row(pre_m4_db_path: Path) -> None:
    url = database_url_for(pre_m4_db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_to(url, PRE_LEAGUE_REVISION)
    connection = sqlite3.connect(pre_m4_db_path)
    rows = connection.execute(
        "SELECT game_pk, hits, runs, strikeouts FROM team_game_batting_lines "
        "ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert rows == [(776704, 6, 4, 9), (776705, 11, 7, 12), (776706, 8, 2, None)]


def test_m4_upgrade_downgrade_upgrade_round_trips(pre_m4_db_path: Path) -> None:
    url = database_url_for(pre_m4_db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_to(url, PRE_LEAGUE_REVISION)
    run_alembic_upgrade(url)
    engine = build_engine(url)
    assert inspect(engine).has_table("league_season_ingestions")
    engine.dispose()
    connection = sqlite3.connect(pre_m4_db_path)
    rows = connection.execute("SELECT count(*) FROM team_game_batting_lines").fetchone()
    connection.close()
    assert rows == (3,)


def test_league_revision_follows_the_strikeouts_revision() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision(LEAGUE_REVISION)
    assert revision.down_revision == PRE_LEAGUE_REVISION


# ---------------------------------------------------------------------------
# Issue #37: baserunner components (walks, hit-by-pitch)
# ---------------------------------------------------------------------------

PRE_BASERUNNERS_REVISION = "7f2c4b8e91d3"
BASERUNNERS_REVISION = "2efdbec9b07e"

# Two rows with real walk and hit-by-pitch totals, plus one row left over from
# before those two columns existed.
PRE_BASERUNNERS_INSERT = """
    INSERT INTO team_game_batting_lines (
        game_pk, game_date, season, team_id, team_name, opponent_id,
        opponent_name, home_away, hits, runs, strikeouts, status, game_number,
        doubleheader, scheduled_innings, created_at, updated_at
    ) VALUES
        (776704, '2025-08-17', 2025, 136, 'Seattle Mariners', 142,
         'Minnesota Twins', 'home', 6, 4, 5, 'Final', 1, 0, 9,
         '2025-08-17 00:00:00', '2025-08-17 00:00:00'),
        (776705, '2025-08-18', 2025, 136, 'Seattle Mariners', 142,
         'Minnesota Twins', 'away', 11, 7, 10, 'Final', 1, 0, 9,
         '2025-08-18 00:00:00', '2025-08-18 00:00:00')
"""


@pytest.fixture
def pre_baserunners_db_path(tmp_path: Path) -> Path:
    """Database at the pre-issue-#37 revision holding two valid rows."""
    db_path = tmp_path / "pre_baserunners.db"
    run_alembic_upgrade_to(database_url_for(db_path), PRE_BASERUNNERS_REVISION)
    connection = sqlite3.connect(db_path)
    connection.execute(PRE_BASERUNNERS_INSERT)
    connection.commit()
    connection.close()
    return db_path


def test_pre_baserunners_revision_has_no_baserunner_columns(
    pre_baserunners_db_path: Path,
) -> None:
    engine = build_engine(database_url_for(pre_baserunners_db_path))
    columns = {
        col["name"] for col in inspect(engine).get_columns("team_game_batting_lines")
    }
    engine.dispose()
    assert "base_on_balls" not in columns
    assert "hit_by_pitch" not in columns


def test_baserunners_upgrade_preserves_existing_rows(
    pre_baserunners_db_path: Path,
) -> None:
    run_alembic_upgrade(database_url_for(pre_baserunners_db_path))
    connection = sqlite3.connect(pre_baserunners_db_path)
    rows = connection.execute(
        "SELECT game_pk, hits, runs, strikeouts FROM team_game_batting_lines "
        "ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert rows == [(776704, 6, 4, 5), (776705, 11, 7, 10)]


def test_baserunners_upgrade_leaves_existing_rows_null(
    pre_baserunners_db_path: Path,
) -> None:
    """Unknown history stays unknown; a zero default would invent a statistic."""
    run_alembic_upgrade(database_url_for(pre_baserunners_db_path))
    connection = sqlite3.connect(pre_baserunners_db_path)
    values = connection.execute(
        "SELECT base_on_balls, hit_by_pitch FROM team_game_batting_lines "
        "ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert values == [(None, None), (None, None)]


def test_baserunners_upgraded_legacy_rows_still_read_as_domain_records(
    pre_baserunners_db_path: Path,
) -> None:
    """The runs page reads these rows through the same repository call."""
    from app.database.engine import build_session_factory
    from app.database.repositories import list_team_season

    run_alembic_upgrade(database_url_for(pre_baserunners_db_path))
    engine = build_engine(database_url_for(pre_baserunners_db_path))
    session = build_session_factory(engine)()
    try:
        stored = list_team_season(session, team_id=136, season=2025)
    finally:
        session.close()
        engine.dispose()

    assert [
        (line.game_pk, line.hits, line.base_on_balls, line.hit_by_pitch)
        for line in stored
    ] == [
        (776704, 6, None, None),
        (776705, 11, None, None),
    ]


def test_baserunners_upgrade_keeps_the_query_index(
    pre_baserunners_db_path: Path,
) -> None:
    """The table is rebuilt in batch mode, so the index must be re-created."""
    run_alembic_upgrade(database_url_for(pre_baserunners_db_path))
    connection = sqlite3.connect(pre_baserunners_db_path)
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name="
        "'ix_team_game_batting_lines_team_season_order'"
    ).fetchall()
    connection.close()
    assert rows == [("ix_team_game_batting_lines_team_season_order",)]


@pytest.mark.parametrize("column", ["hits", "runs", "strikeouts"])
def test_baserunners_upgrade_keeps_the_existing_check_constraints(
    pre_baserunners_db_path: Path, column: str
) -> None:
    """A batch rebuild driven by reflection alone would have dropped these."""
    run_alembic_upgrade(database_url_for(pre_baserunners_db_path))
    connection = sqlite3.connect(pre_baserunners_db_path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"""
            INSERT INTO team_game_batting_lines (
                game_pk, game_date, season, team_id, team_name, opponent_id,
                opponent_name, home_away, hits, runs, strikeouts, status,
                game_number, doubleheader, scheduled_innings, created_at,
                updated_at
            ) VALUES (
                900, '2025-01-01', 2025, 136, 'A', 142, 'B', 'home',
                {-1 if column == "hits" else 1},
                {-1 if column == "runs" else 1},
                {-1 if column == "strikeouts" else 1},
                'Final', 1, 0, 9, '2025-01-01 00:00:00', '2025-01-01 00:00:00'
            )
            """
        )
    connection.close()


@pytest.mark.parametrize("column", ["base_on_balls", "hit_by_pitch"])
def test_negative_baserunner_component_is_rejected(
    migrated_session: Session, column: str
) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                f"""
                INSERT INTO team_game_batting_lines (
                    game_pk, game_date, season, team_id, team_name, opponent_id,
                    opponent_name, home_away, hits, runs, status, game_number,
                    doubleheader, scheduled_innings, created_at, updated_at,
                    {column}
                ) VALUES (
                    30, '2025-01-01', 2025, 112, 'A', 134, 'B', 'home',
                    1, 1, 'Final', 1, 0, 9, '2025-01-01 00:00:00',
                    '2025-01-01 00:00:00', -1
                )
                """
            )
        )
        migrated_session.commit()


@pytest.mark.parametrize("column", ["base_on_balls", "hit_by_pitch"])
@pytest.mark.parametrize("value", ["NULL", "0", "5"])
def test_null_and_nonnegative_baserunner_components_are_accepted(
    migrated_session: Session, column: str, value: str
) -> None:
    migrated_session.execute(
        text(
            f"""
            INSERT INTO team_game_batting_lines (
                game_pk, game_date, season, team_id, team_name, opponent_id,
                opponent_name, home_away, hits, runs, status, game_number,
                doubleheader, scheduled_innings, created_at, updated_at,
                {column}
            ) VALUES (
                {40 + len(column) + len(value)}, '2025-01-01', 2025, 112, 'A',
                134, 'B', 'home', 1, 1, 'Final', 1, 0, 9,
                '2025-01-01 00:00:00', '2025-01-01 00:00:00', {value}
            )
            """
        )
    )
    migrated_session.commit()


def test_baserunners_downgrade_removes_the_two_columns(
    pre_baserunners_db_path: Path,
) -> None:
    url = database_url_for(pre_baserunners_db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_to(url, PRE_BASERUNNERS_REVISION)
    engine = build_engine(url)
    columns = {
        col["name"] for col in inspect(engine).get_columns("team_game_batting_lines")
    }
    engine.dispose()
    assert "base_on_balls" not in columns
    assert "hit_by_pitch" not in columns


def test_baserunners_downgrade_keeps_the_other_columns_and_rows(
    pre_baserunners_db_path: Path,
) -> None:
    url = database_url_for(pre_baserunners_db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_to(url, PRE_BASERUNNERS_REVISION)
    connection = sqlite3.connect(pre_baserunners_db_path)
    rows = connection.execute(
        "SELECT game_pk, hits, runs, strikeouts FROM team_game_batting_lines "
        "ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert rows == [(776704, 6, 4, 5), (776705, 11, 7, 10)]


def test_baserunners_upgrade_downgrade_upgrade_round_trips(
    pre_baserunners_db_path: Path,
) -> None:
    url = database_url_for(pre_baserunners_db_path)
    run_alembic_upgrade(url)
    run_alembic_downgrade_to(url, PRE_BASERUNNERS_REVISION)
    run_alembic_upgrade(url)
    connection = sqlite3.connect(pre_baserunners_db_path)
    rows = connection.execute(
        "SELECT game_pk, base_on_balls, hit_by_pitch FROM team_game_batting_lines "
        "ORDER BY game_pk"
    ).fetchall()
    connection.close()
    assert rows == [(776704, None, None), (776705, None, None)]


def test_baserunners_revision_follows_the_league_revision() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision(BASERUNNERS_REVISION)
    assert revision.down_revision == PRE_BASERUNNERS_REVISION
