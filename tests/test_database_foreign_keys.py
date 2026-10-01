"""Tests that application SQLite connections enforce declared foreign keys.

SQLite ignores ``FOREIGN KEY`` clauses unless each connection enables
``PRAGMA foreign_keys``. These tests prove enforcement against a database
migrated through the project's Alembic path, using the same ``build_engine``
path the application, CLI scripts, and repository tests use.

Alembic itself runs on its own engine (``alembic/env.py``) without the
pragma. Nothing here runs migrations with enforcement on; the migration test
only checks the migrated result through an enforcing application engine.
"""

import sqlite3
from collections.abc import Generator
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import engine as engine_module
from app.database.engine import (
    SQLiteForeignKeyEnforcementError,
    _enable_sqlite_foreign_keys,
    build_engine,
    build_session_factory,
)
from app.database.models import (
    PlayerRecord,
    PlayerSeasonCatalogRecord,
    PlayerSeasonHittingRecord,
)
from app.database.repositories import (
    ensure_player_season_catalog_membership,
    get_player,
    get_player_catalog_entry,
    get_player_season_hitting,
    upsert_player,
    upsert_player_season_hitting,
)
from app.schemas.ingestion import PlayerPersistenceOutcome
from app.schemas.players import PlayerIdentity, PlayerSeasonHitting
from app.services.player_catalog_ingestion import ingest_player_catalog
from app.services.player_season_ingestion import ingest_player_season
from tests.conftest import run_alembic_upgrade
from tests.test_player_catalog_ingestion import FakeDirectory
from tests.test_player_catalog_ingestion import make_person as make_catalog_person
from tests.test_players_service import FakeMlb, make_person, make_split, make_stat

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


@pytest.fixture(params=["declaration_order", "players_flushed_last"])
def unit_of_work_order(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Run a valid-write test under both possible unit-of-work INSERT orders.

    The Player models declare ``ForeignKey``s but no ``relationship()``, so
    SQLAlchemy's unit of work has no parent/child edge between the mappers and
    orders their INSERT batches by ``mapper._sort_key`` (module + class name).
    ``PlayerRecord`` currently sorts first only by alphabetical coincidence.
    ``players_flushed_last`` simulates a rename that would sort it last, so
    these tests prove valid writes do not depend on class names.
    """
    if request.param == "players_flushed_last":
        monkeypatch.setattr(PlayerRecord.__mapper__, "_sort_key", "~players_last")
    return request.param


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


def test_new_player_identity_and_membership_succeed_in_one_transaction(
    migrated_session: Session, unit_of_work_order: str
) -> None:
    identity = make_identity()
    with migrated_session.begin():
        upsert_player(migrated_session, identity=identity)
        ensure_player_season_catalog_membership(
            migrated_session, identity=identity, season=SEASON
        )

    assert get_player(migrated_session, player_id=PLAYER_ID) == identity
    assert (
        get_player_catalog_entry(migrated_session, player_id=PLAYER_ID, season=SEASON)
        is not None
    )


def test_new_player_identity_membership_and_hitting_succeed_in_one_transaction(
    migrated_session: Session, unit_of_work_order: str
) -> None:
    identity = make_identity()
    with migrated_session.begin():
        upsert_player(migrated_session, identity=identity)
        ensure_player_season_catalog_membership(
            migrated_session, identity=identity, season=SEASON
        )
        upsert_player_season_hitting(migrated_session, hitting=make_hitting())

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


def test_failed_child_write_rolls_back_flushed_new_player(
    migrated_session: Session,
) -> None:
    """The early parent flush stays inside the caller's single transaction."""
    with pytest.raises(IntegrityError), migrated_session.begin():
        upsert_player(migrated_session, identity=make_identity())
        # A hitting row for a different, unknown player is still an orphan.
        upsert_player_season_hitting(
            migrated_session,
            hitting=make_hitting().model_copy(update={"player_id": 1}),
        )

    assert count_rows(migrated_session, PlayerRecord) == 0
    assert count_rows(migrated_session, PlayerSeasonHittingRecord) == 0


def test_player_season_ingestion_succeeds_and_reruns_with_enforcement(
    migrated_session: Session, unit_of_work_order: str
) -> None:
    client = FakeMlb(
        person=make_person(),
        player_stats={"hitting": {"season": make_stat([make_split()])}},
    )

    first = ingest_player_season(
        session=migrated_session, player_id=PLAYER_ID, season=SEASON, client=client
    )
    rerun = ingest_player_season(
        session=migrated_session, player_id=PLAYER_ID, season=SEASON, client=client
    )

    assert first.identity_outcome is PlayerPersistenceOutcome.INSERTED
    assert first.hitting_outcome is PlayerPersistenceOutcome.INSERTED
    assert rerun.identity_outcome is PlayerPersistenceOutcome.UNCHANGED
    assert rerun.hitting_outcome is PlayerPersistenceOutcome.UNCHANGED
    assert count_rows(migrated_session, PlayerRecord) == 1
    assert count_rows(migrated_session, PlayerSeasonCatalogRecord) == 1
    assert count_rows(migrated_session, PlayerSeasonHittingRecord) == 1


def test_player_catalog_ingestion_succeeds_and_reruns_with_enforcement(
    migrated_session: Session, unit_of_work_order: str
) -> None:
    people = [
        make_catalog_person(player_id=PLAYER_ID, full_name="Julio Rodriguez"),
        make_catalog_person(player_id=663728, full_name="Cal Raleigh"),
    ]

    first = ingest_player_catalog(
        session=migrated_session, season=SEASON, client=FakeDirectory(people)
    )
    renamed = [people[0], make_catalog_person(player_id=663728, full_name="Big Dumper")]
    rerun = ingest_player_catalog(
        session=migrated_session, season=SEASON, client=FakeDirectory(renamed)
    )
    next_season = ingest_player_catalog(
        session=migrated_session, season=SEASON + 1, client=FakeDirectory(renamed)
    )

    assert (first.inserted, first.updated, first.unchanged) == (2, 0, 0)
    assert (rerun.inserted, rerun.updated, rerun.unchanged) == (0, 1, 1)
    assert next_season.inserted == 2
    assert count_rows(migrated_session, PlayerRecord) == 2
    assert count_rows(migrated_session, PlayerSeasonCatalogRecord) == 4


def test_freshly_migrated_database_has_no_foreign_key_violations(
    tmp_path: Path,
) -> None:
    """Upgrade through the project's Alembic path, then inspect with enforcement.

    Alembic runs on its own non-enforcing engine; this verifies the migrated
    result, not migration execution under enforcement.
    """
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


def test_hook_enables_foreign_keys_even_if_a_transaction_is_open() -> None:
    """SQLite ignores the pragma inside a transaction; the hook must not."""
    connection = sqlite3.connect(":memory:", autocommit=False)
    try:
        assert connection.in_transaction

        _enable_sqlite_foreign_keys(connection, Mock())

        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert connection.autocommit is False
    finally:
        connection.close()


def test_hook_restores_legacy_transaction_control() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        _enable_sqlite_foreign_keys(connection, Mock())

        assert connection.autocommit == sqlite3.LEGACY_TRANSACTION_CONTROL
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    finally:
        connection.close()


def test_hook_rejects_non_sqlite3_connection() -> None:
    with pytest.raises(SQLiteForeignKeyEnforcementError, match="sqlite3"):
        _enable_sqlite_foreign_keys(Mock(), Mock())
