"""Tests for the team-season ingestion service."""

from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.models import TeamGameBattingLineRecord
from app.database.repositories import list_team_season, upsert_team_season
from app.schemas.ingestion import TeamGamePersistenceResult
from app.services.team_game_logs import (
    TeamGameDataError,
    TeamGameLogError,
    TeamNotFoundError,
    get_team_game_batting_lines,
)
from app.services.team_season_ingestion import (
    TeamSeasonIngestionError,
    ingest_team_season,
)
from tests.test_repositories import CUBS_ID, SEASON, make_line
from tests.test_team_game_logs import FakeMlb, make_client


def test_first_import_reports_all_inserted(migrated_session: Session) -> None:
    client = make_client()
    result = ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=client,
    )
    assert result.fetched == 6
    assert result.inserted == 6
    assert result.updated == 0
    assert result.unchanged == 0


def test_second_identical_import_reports_unchanged(migrated_session: Session) -> None:
    client = make_client()
    ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=client,
    )
    result = ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=client,
    )
    assert result.inserted == 0
    assert result.updated == 0
    assert result.unchanged == 6


def test_changed_source_data_updates_without_duplicate(
    migrated_session: Session,
) -> None:
    client = make_client()
    ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=client,
    )
    line = make_line(game_pk=776704, hits=99)
    with patch(
        "app.services.team_season_ingestion.get_team_game_batting_lines",
        return_value=[line],
    ):
        result = ingest_team_season(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
            client=client,
        )
    assert result.updated == 1
    count = migrated_session.scalar(
        select(func.count()).select_from(TeamGameBattingLineRecord)
    )
    assert count == 6


def test_result_satisfies_count_invariant(migrated_session: Session) -> None:
    client = make_client()
    result = ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=client,
    )
    assert result.fetched == result.inserted + result.updated + result.unchanged


def test_mlb_retrieval_error_leaves_database_unchanged(
    migrated_session: Session,
) -> None:
    client = FakeMlb(team=TeamNotFoundError("missing"))
    with pytest.raises(TeamNotFoundError):
        ingest_team_season(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
            client=client,
        )
    count = migrated_session.scalar(
        select(func.count()).select_from(TeamGameBattingLineRecord)
    )
    assert count == 0


def test_normalization_error_leaves_database_unchanged(
    migrated_session: Session,
) -> None:
    with (
        patch(
            "app.services.team_season_ingestion.get_team_game_batting_lines",
            side_effect=TeamGameDataError("bad data"),
        ),
        pytest.raises(TeamGameDataError),
    ):
        ingest_team_season(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
        )
    count = migrated_session.scalar(
        select(func.count()).select_from(TeamGameBattingLineRecord)
    )
    assert count == 0


def test_database_error_rolls_back_writes(migrated_session: Session) -> None:
    with (
        patch(
            "app.services.team_season_ingestion.get_team_game_batting_lines",
            return_value=[make_line()],
        ),
        patch(
            "app.services.team_season_ingestion.upsert_team_season",
            side_effect=SQLAlchemyError("db failed"),
        ),
        pytest.raises(TeamSeasonIngestionError),
    ):
        ingest_team_season(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
        )
    count = migrated_session.scalar(
        select(func.count()).select_from(TeamGameBattingLineRecord)
    )
    assert count == 0


def test_no_partial_team_season_after_failed_transaction(
    migrated_session: Session,
) -> None:
    lines = [make_line(game_pk=1), make_line(game_pk=2, game_date="2025-04-02")]

    def failing_upsert(session: Session, *, lines: list) -> TeamGamePersistenceResult:
        from app.database.repositories import upsert_team_season as real_upsert

        real_upsert(session, lines=lines)
        raise SQLAlchemyError("fail after staging writes")

    with (
        patch(
            "app.services.team_season_ingestion.get_team_game_batting_lines",
            return_value=lines,
        ),
        patch(
            "app.services.team_season_ingestion.upsert_team_season",
            side_effect=failing_upsert,
        ),
        pytest.raises(TeamSeasonIngestionError),
    ):
        ingest_team_season(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
        )
    count = migrated_session.scalar(
        select(func.count()).select_from(TeamGameBattingLineRecord)
    )
    assert count == 0


def test_milestone_one_service_runs_before_transaction(
    migrated_session: Session,
) -> None:
    fetch_completed = False

    def fetch_side_effect(*args: object, **kwargs: object) -> list:
        nonlocal fetch_completed
        fetch_completed = True
        return [make_line()]

    def upsert_side_effect(
        session: Session, *, lines: list
    ) -> TeamGamePersistenceResult:
        assert fetch_completed
        return TeamGamePersistenceResult(inserted=1, updated=0, unchanged=0)

    with (
        patch(
            "app.services.team_season_ingestion.get_team_game_batting_lines",
            side_effect=fetch_side_effect,
        ),
        patch(
            "app.services.team_season_ingestion.upsert_team_season",
            side_effect=upsert_side_effect,
        ),
    ):
        ingest_team_season(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
        )


def test_supplied_mlb_client_is_supported(migrated_session: Session) -> None:
    client = make_client()
    result = ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=client,
    )
    assert result.fetched == 6
    assert client.calls


def test_team_game_log_error_is_not_wrapped(migrated_session: Session) -> None:
    with (
        patch(
            "app.services.team_season_ingestion.get_team_game_batting_lines",
            side_effect=TeamGameLogError("network"),
        ),
        pytest.raises(TeamGameLogError),
    ):
        ingest_team_season(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
        )


def test_import_persists_batting_strikeouts(migrated_session: Session) -> None:
    """The existing command is the only strikeout ingestion path."""
    ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=make_client(),
    )
    stored = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    assert [line.strikeouts for line in stored] == [5, 8, 9, 9, 10, 6]


def test_import_backfills_legacy_rows_and_is_then_idempotent(
    migrated_session: Session,
) -> None:
    """Milestone 3.5's regression path, end to end, without touching MLB."""
    legacy = [
        line.model_copy(update={"strikeouts": None})
        for line in get_team_game_batting_lines(CUBS_ID, SEASON, client=make_client())
    ]
    upsert_team_season(migrated_session, lines=legacy)
    migrated_session.commit()
    stored = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    assert all(line.strikeouts is None for line in stored)
    # ``ingest_team_season`` opens its own transaction, so close the implicit
    # one the read above started.
    migrated_session.commit()

    backfill = ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=make_client(),
    )
    assert (backfill.inserted, backfill.updated, backfill.unchanged) == (0, 6, 0)

    again = ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=make_client(),
    )
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 6)

    stored = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    assert [line.strikeouts for line in stored] == [5, 8, 9, 9, 10, 6]


def test_import_makes_exactly_three_mlb_requests(migrated_session: Session) -> None:
    """Strikeouts did not add a fourth request to the team-season strategy."""
    client = make_client()
    ingest_team_season(
        session=migrated_session,
        team_id=CUBS_ID,
        season=SEASON,
        client=client,
    )
    assert sorted(client.calls) == ["get_schedule", "get_team", "get_team_stats"]
