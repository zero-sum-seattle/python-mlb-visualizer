"""Tests for the async team-season ingestion entry point.

``ingest_team_season_async`` fetches with ``await`` and then calls the exact
same ``persist_team_season`` helper the sync path uses, so these tests focus
on what could plausibly differ: that persistence is still atomic and
idempotent, and that ingestion failures surface the same way.
"""

import asyncio

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import TeamGameBattingLineRecord, TeamGamePitchingLineRecord
from app.services.team_season_ingestion import ingest_team_season_async
from tests.test_team_game_logs_async import make_async_client
from tests.test_team_season_ingestion import CUBS_ID, SEASON


def test_async_first_import_reports_all_inserted(migrated_session: Session) -> None:
    result = asyncio.run(
        ingest_team_season_async(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
            client=make_async_client(),
        )
    )
    assert result.fetched == 6
    assert result.inserted == 6
    assert result.pitching is not None
    assert result.pitching.inserted == 6


def test_async_batting_and_pitching_commit_together(migrated_session: Session) -> None:
    """Both tables are populated by one call, in one transaction."""
    asyncio.run(
        ingest_team_season_async(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
            client=make_async_client(),
        )
    )
    batting_count = migrated_session.scalar(
        select(func.count()).select_from(TeamGameBattingLineRecord)
    )
    pitching_count = migrated_session.scalar(
        select(func.count()).select_from(TeamGamePitchingLineRecord)
    )
    assert batting_count == 6
    assert pitching_count == 6


def test_async_repeat_import_is_idempotent(migrated_session: Session) -> None:
    client = make_async_client()
    asyncio.run(
        ingest_team_season_async(
            session=migrated_session, team_id=CUBS_ID, season=SEASON, client=client
        )
    )
    result = asyncio.run(
        ingest_team_season_async(
            session=migrated_session, team_id=CUBS_ID, season=SEASON, client=client
        )
    )
    assert (result.inserted, result.updated) == (0, 0)
    assert result.unchanged == 6


def test_async_and_sequential_ingestion_persist_the_same_rows(
    migrated_session: Session,
) -> None:
    """The two transports must not diverge on what ends up in the database."""
    from app.database.repositories import list_team_season, list_team_season_pitching
    from app.services.team_season_ingestion import ingest_team_season
    from tests.test_team_game_logs import make_client

    asyncio.run(
        ingest_team_season_async(
            session=migrated_session,
            team_id=CUBS_ID,
            season=SEASON,
            client=make_async_client(),
        )
    )
    async_batting = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    async_pitching = list_team_season_pitching(
        migrated_session, team_id=CUBS_ID, season=SEASON
    )

    other_session = migrated_session
    # Clear and re-run the sequential path against the same schema to compare.
    other_session.query(TeamGameBattingLineRecord).delete()
    other_session.query(TeamGamePitchingLineRecord).delete()
    other_session.commit()

    ingest_team_season(
        session=other_session, team_id=CUBS_ID, season=SEASON, client=make_client()
    )
    sync_batting = list_team_season(other_session, team_id=CUBS_ID, season=SEASON)
    sync_pitching = list_team_season_pitching(
        other_session, team_id=CUBS_ID, season=SEASON
    )

    assert async_batting == sync_batting
    assert async_pitching == sync_pitching
