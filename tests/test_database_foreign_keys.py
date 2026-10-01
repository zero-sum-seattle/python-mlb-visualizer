"""Tests that application SQLite connections enforce declared foreign keys.

SQLite ignores ``FOREIGN KEY`` clauses unless each connection enables
``PRAGMA foreign_keys``. These tests prove enforcement against a database
migrated through Alembic, using the same ``build_engine`` path the
application, CLI scripts, and repository tests use.
"""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import engine as engine_module
from app.database.engine import build_engine, build_session_factory
from app.database.models import PlayerSeasonCatalogRecord, PlayerSeasonHittingRecord
from app.database.repositories import (
    ensure_player_season_catalog_membership,
    get_player,
    get_player_catalog_entry,
    get_player_season_hitting,
    upsert_player,
    upsert_player_season_hitting,
)
from app.schemas.players import PlayerIdentity, PlayerSeasonHitting
from tests.conftest import run_alembic_upgrade

PLAYER_ID = 677594
SEASON = 2025


def make_identity() -> PlayerIdentity:
    return PlayerIdentity(
        player_id=PLAYER_ID, full_name="Julio Rodriguez", primary_position="CF"
    )


def make_hitting() -> PlayerSeasonHitting:
    return PlayerSeasonHitting(
        player_id=PLAYER_ID,
        season=SEASON,
        games_played=150,
        plate_appearances=600,
        at_bats=500,
        runs=80,
        hits=150,
        doubles=30,
        triples=3,
        home_runs=20,
        rbi=90,
        base_on_balls=60,
        intentional_walks=5,
        hit_by_pitch=5,
        strikeouts=100,
        stolen_bases=10,
        caught_stealing=3,
        sac_flies=4,
        sac_bunts=2,
    )


@pytest.fixture
def migrated_engine(migrated_db_path: Path) -> Generator[Engine, None, None]:
    engine = build_engine(f"sqlite:///{migrated_db_path}")
    try:
        yield engine
    finally:
        engine.dispose()


def foreign_keys_pragma(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("PRAGMA foreign_keys")).scalar_one()


def count_rows(session: Session, model: type) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_sqlite_engine_connections_enable_foreign_keys(
    migrated_engine: Engine,
) -> None:
    assert foreign_keys_pragma(migrated_engine) == 1


def test_every_new_sqlite_connection_enables_foreign_keys(
    migrated_engine: Engine,
) -> None:
    # Hold two connections at once so the pool must open a second DBAPI
    # connection rather than reusing the first.
    with migrated_engine.connect() as first, migrated_engine.connect() as second:
        assert first.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert second.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_orphan_player_season_membership_is_rejected(
    migrated_session: Session,
) -> None:
    ensure_player_season_catalog_membership(
        migrated_session, identity=make_identity(), season=SEASON
    )

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        migrated_session.commit()

    migrated_session.rollback()
    assert count_rows(migrated_session, PlayerSeasonCatalogRecord) == 0


def test_orphan_player_season_hitting_is_rejected(
    migrated_session: Session,
) -> None:
    upsert_player_season_hitting(migrated_session, hitting=make_hitting())

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        migrated_session.commit()

    migrated_session.rollback()
    assert count_rows(migrated_session, PlayerSeasonHittingRecord) == 0


def test_raw_orphan_player_season_hitting_insert_is_rejected(
    migrated_engine: Engine,
) -> None:
    """Enforcement is a database guarantee, not an ORM side effect."""
    with (
        pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"),
        migrated_engine.begin() as connection,
    ):
        connection.execute(
            text(
                """
                INSERT INTO player_season_hitting (
                    player_id, season, games_played, plate_appearances,
                    at_bats, runs, hits, doubles, triples, home_runs, rbi,
                    base_on_balls, intentional_walks, hit_by_pitch,
                    strikeouts, stolen_bases, caught_stealing, sac_flies,
                    sac_bunts, created_at, updated_at
                ) VALUES (
                    999999, 2025, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, '2025-01-01 00:00:00',
                    '2025-01-01 00:00:00'
                )
                """
            )
        )


def test_valid_player_parent_child_persistence_succeeds_in_one_commit(
    migrated_session: Session,
) -> None:
    """Identity, membership, and hitting added together flush parent-first."""
    identity = make_identity()
    upsert_player(migrated_session, identity=identity)
    ensure_player_season_catalog_membership(
        migrated_session, identity=identity, season=SEASON
    )
    upsert_player_season_hitting(migrated_session, hitting=make_hitting())
    migrated_session.commit()

    assert get_player(migrated_session, player_id=PLAYER_ID) == identity
    catalog_entry = get_player_catalog_entry(
        migrated_session, player_id=PLAYER_ID, season=SEASON
    )
    assert catalog_entry is not None
    assert catalog_entry.player_id == PLAYER_ID
    assert (
        get_player_season_hitting(migrated_session, player_id=PLAYER_ID, season=SEASON)
        == make_hitting()
    )


def test_freshly_migrated_database_has_no_foreign_key_violations(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fresh.db"
    url = f"sqlite:///{db_path}"
    run_alembic_upgrade(url)

    engine = build_engine(url)
    try:
        assert foreign_keys_pragma(engine) == 1
        with engine.connect() as connection:
            violations = connection.execute(text("PRAGMA foreign_key_check")).all()
        assert violations == []
    finally:
        engine.dispose()


def test_non_sqlite_url_does_not_install_sqlite_foreign_key_pragma() -> None:
    """A non-SQLite URL must not be coupled to SQLite connection setup.

    No PostgreSQL driver is installed, so ``create_engine`` is intercepted and
    returns an in-memory SQLite engine. If ``build_engine`` wrongly attached
    the SQLite hook, that engine's connections would report the pragma on.
    """
    stand_in = create_engine("sqlite://")
    with patch.object(
        engine_module, "create_engine", return_value=stand_in
    ) as fake_create_engine:
        engine = build_engine("postgresql://user@localhost/mlb")

    try:
        fake_create_engine.assert_called_once_with(
            "postgresql://user@localhost/mlb", connect_args={}
        )
        assert foreign_keys_pragma(engine) == 0
    finally:
        engine.dispose()


def test_session_factory_sessions_enforce_foreign_keys(
    migrated_engine: Engine,
) -> None:
    session = build_session_factory(migrated_engine)()
    try:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        session.close()
