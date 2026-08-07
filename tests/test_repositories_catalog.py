"""Tests for the read queries that populate the team and season selectors."""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database.engine import build_engine, build_session_factory
from app.database.repositories import (
    DatabaseSchemaMissingError,
    list_available_team_seasons,
    upsert_team_season,
)
from tests.factories import make_season


def store(session: Session, **kwargs: object) -> None:
    upsert_team_season(session, lines=make_season(**kwargs))
    session.commit()


def test_empty_database_returns_no_team_seasons(migrated_session: Session) -> None:
    assert list_available_team_seasons(migrated_session) == []


def test_persisted_team_season_is_returned(migrated_session: Session) -> None:
    store(migrated_session, hits=[8, 9, 10])
    available = list_available_team_seasons(migrated_session)
    assert len(available) == 1
    assert available[0].team_id == 136
    assert available[0].team_name == "Seattle Mariners"
    assert available[0].season == 2025
    assert available[0].games_played == 3


def test_many_game_rows_produce_one_selector_entry(migrated_session: Session) -> None:
    store(migrated_session, hits=[7] * 162)
    available = list_available_team_seasons(migrated_session)
    assert len(available) == 1
    assert available[0].games_played == 162


def test_reimporting_the_same_season_does_not_duplicate_entries(
    migrated_session: Session,
) -> None:
    lines = make_season([5, 6, 7])
    upsert_team_season(migrated_session, lines=lines)
    migrated_session.commit()
    upsert_team_season(migrated_session, lines=lines)
    migrated_session.commit()
    assert len(list_available_team_seasons(migrated_session)) == 1


def test_each_season_of_a_team_is_returned(migrated_session: Session) -> None:
    store(migrated_session, hits=[8], season=2024)
    store(migrated_session, hits=[9], season=2025)
    seasons = [
        entry.season
        for entry in list_available_team_seasons(migrated_session)
        if entry.team_id == 136
    ]
    assert seasons == [2025, 2024]


def test_historical_team_names_are_preserved_per_season(
    migrated_session: Session,
) -> None:
    store(
        migrated_session,
        hits=[6],
        season=2021,
        team_id=114,
        team_name="Cleveland Indians",
    )
    store(
        migrated_session,
        hits=[7],
        season=2022,
        team_id=114,
        team_name="Cleveland Guardians",
    )
    names = {
        entry.season: entry.team_name
        for entry in list_available_team_seasons(migrated_session)
    }
    assert names == {2021: "Cleveland Indians", 2022: "Cleveland Guardians"}


def test_ordering_is_alphabetical_then_newest_season_first(
    migrated_session: Session,
) -> None:
    store(migrated_session, hits=[4], team_id=112, team_name="Chicago Cubs")
    store(migrated_session, hits=[5], season=2024)
    store(migrated_session, hits=[6], season=2025)
    assert [
        (entry.team_name, entry.season)
        for entry in list_available_team_seasons(migrated_session)
    ] == [
        ("Chicago Cubs", 2025),
        ("Seattle Mariners", 2025),
        ("Seattle Mariners", 2024),
    ]


def test_repeated_calls_return_the_same_order(migrated_session: Session) -> None:
    store(migrated_session, hits=[4], team_id=112, team_name="Chicago Cubs")
    store(migrated_session, hits=[5], team_id=147, team_name="New York Yankees")
    store(migrated_session, hits=[6])
    first = list_available_team_seasons(migrated_session)
    assert first == list_available_team_seasons(migrated_session)


@pytest.fixture
def unmigrated_session(tmp_path: Path) -> Generator[Session]:
    """Session against a reachable SQLite file that has no tables."""
    engine = build_engine(f"sqlite:///{tmp_path / 'unmigrated.db'}")
    session = build_session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_missing_table_raises_a_schema_error(unmigrated_session: Session) -> None:
    with pytest.raises(DatabaseSchemaMissingError):
        list_available_team_seasons(unmigrated_session)


def test_missing_table_error_gives_the_migration_command(
    unmigrated_session: Session,
) -> None:
    with pytest.raises(DatabaseSchemaMissingError, match="alembic upgrade head"):
        list_available_team_seasons(unmigrated_session)


def test_missing_table_error_keeps_the_original_exception_as_its_cause(
    unmigrated_session: Session,
) -> None:
    with pytest.raises(DatabaseSchemaMissingError) as caught:
        list_available_team_seasons(unmigrated_session)
    assert isinstance(caught.value.__cause__, OperationalError)


def test_an_unreadable_database_is_not_reported_as_a_missing_migration(
    tmp_path: Path,
) -> None:
    """A path that cannot be opened is not something a migration would fix."""
    engine = build_engine(f"sqlite:///{tmp_path / 'no-such-directory' / 'x.db'}")
    session = build_session_factory(engine)()
    try:
        with pytest.raises(OperationalError) as caught:
            list_available_team_seasons(session)
        assert not isinstance(caught.value, DatabaseSchemaMissingError)
    finally:
        session.close()
        engine.dispose()


def test_a_locked_database_is_not_reported_as_a_missing_migration() -> None:
    locked = OperationalError("SELECT 1", None, Exception("database is locked"))
    session = Mock(spec=Session)
    session.execute.side_effect = locked

    with pytest.raises(OperationalError) as caught:
        list_available_team_seasons(session)
    assert caught.value is locked


def test_a_different_missing_table_is_not_reported_as_a_missing_migration() -> None:
    """Only our own table's absence means this application needs migrating."""
    unrelated = OperationalError(
        "SELECT 1", None, Exception("no such table: alembic_version")
    )
    session = Mock(spec=Session)
    session.execute.side_effect = unrelated

    with pytest.raises(OperationalError) as caught:
        list_available_team_seasons(session)
    assert caught.value is unrelated
