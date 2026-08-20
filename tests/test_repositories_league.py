"""Tests for league-season ingestion coverage persistence."""

from datetime import datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import LeagueSeasonIngestionRecord
from app.database.repositories import (
    get_league_season_ingestion,
    record_league_season_ingestion_finish,
    record_league_season_ingestion_start,
)
from app.schemas.ingestion import (
    LeagueSeasonIngestionState,
    LeagueSeasonIngestionStatus,
)

SEASON = 2025
STARTED = datetime(2026, 3, 1, 12, 0, 0)
FINISHED = datetime(2026, 3, 1, 12, 5, 0)


def start(session: Session, *, season: int = SEASON, teams: int = 30) -> None:
    with session.begin():
        record_league_season_ingestion_start(
            session, season=season, expected_team_count=teams, started_at=STARTED
        )


def finish(
    session: Session,
    *,
    season: int = SEASON,
    teams: int = 30,
    succeeded: int = 30,
    failed: int = 0,
    completed_at: datetime = FINISHED,
) -> LeagueSeasonIngestionState:
    with session.begin():
        return record_league_season_ingestion_finish(
            session,
            season=season,
            expected_team_count=teams,
            successful_team_count=succeeded,
            failed_team_count=failed,
            started_at=STARTED,
            completed_at=completed_at,
        )


def row_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(LeagueSeasonIngestionRecord))


def test_no_state_before_any_league_ingestion(migrated_session: Session) -> None:
    assert get_league_season_ingestion(migrated_session, season=SEASON) is None


def test_start_records_running_state(migrated_session: Session) -> None:
    start(migrated_session)
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.RUNNING
    assert state.expected_team_count == 30
    assert (state.successful_team_count, state.failed_team_count) == (0, 0)
    assert state.started_at == STARTED
    assert state.completed_at is None


def test_finish_records_complete_coverage(migrated_session: Session) -> None:
    start(migrated_session)
    finish(migrated_session)
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.COMPLETE
    assert state.successful_team_count == 30
    assert state.completed_at == FINISHED


def test_finish_records_incomplete_coverage_when_a_team_failed(
    migrated_session: Session,
) -> None:
    start(migrated_session)
    state = finish(migrated_session, succeeded=29, failed=1)
    assert state.status is LeagueSeasonIngestionStatus.INCOMPLETE
    assert (state.successful_team_count, state.failed_team_count) == (29, 1)


def test_the_status_is_derived_not_supplied(migrated_session: Session) -> None:
    """A caller cannot ask for COMPLETE while a discovered team failed."""
    start(migrated_session)
    state = finish(migrated_session, teams=30, succeeded=29, failed=1)
    assert state.status is not LeagueSeasonIngestionStatus.COMPLETE


def test_zero_expected_teams_is_never_complete(migrated_session: Session) -> None:
    state = finish(migrated_session, teams=0, succeeded=0, failed=0)
    assert state.status is LeagueSeasonIngestionStatus.INCOMPLETE


def test_a_season_keeps_one_row_across_reruns(migrated_session: Session) -> None:
    start(migrated_session)
    finish(migrated_session, succeeded=29, failed=1)
    start(migrated_session)
    finish(migrated_session)
    assert row_count(migrated_session) == 1
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.COMPLETE
    assert state.failed_team_count == 0


def test_a_new_run_invalidates_the_previous_answer(migrated_session: Session) -> None:
    """While a rerun is in flight the old COMPLETE must not still be readable."""
    start(migrated_session)
    finish(migrated_session)
    start(migrated_session)
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.RUNNING
    assert state.completed_at is None


def test_seasons_are_tracked_independently(migrated_session: Session) -> None:
    start(migrated_session, season=2024)
    finish(migrated_session, season=2024, succeeded=29, failed=1)
    start(migrated_session, season=2025)
    finish(migrated_session, season=2025)
    states = {
        season: get_league_season_ingestion(migrated_session, season=season)
        for season in (2024, 2025)
    }
    assert states[2024] is not None and states[2025] is not None
    assert states[2024].status is LeagueSeasonIngestionStatus.INCOMPLETE
    assert states[2025].status is LeagueSeasonIngestionStatus.COMPLETE
    assert row_count(migrated_session) == 2


def test_a_second_row_for_a_season_is_rejected(migrated_session: Session) -> None:
    start(migrated_session)
    migrated_session.commit()
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO league_season_ingestions (
                    season, status, expected_team_count, successful_team_count,
                    failed_team_count, started_at, completed_at
                ) VALUES (
                    2025, 'RUNNING', 30, 0, 0, '2026-03-02 00:00:00', NULL
                )
                """
            )
        )
        migrated_session.commit()


def test_a_partial_run_cannot_be_stored_as_complete(migrated_session: Session) -> None:
    """The database refuses the claim even if application code stops enforcing it."""
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
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
        )
        migrated_session.commit()


def test_a_complete_run_with_no_teams_is_rejected(migrated_session: Session) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO league_season_ingestions (
                    season, status, expected_team_count, successful_team_count,
                    failed_team_count, started_at, completed_at
                ) VALUES (
                    2025, 'COMPLETE', 0, 0, 0, '2026-03-01 12:00:00',
                    '2026-03-01 12:05:00'
                )
                """
            )
        )
        migrated_session.commit()


def test_a_finished_run_must_have_a_completion_time(
    migrated_session: Session,
) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO league_season_ingestions (
                    season, status, expected_team_count, successful_team_count,
                    failed_team_count, started_at, completed_at
                ) VALUES (
                    2025, 'INCOMPLETE', 30, 29, 1, '2026-03-01 12:00:00', NULL
                )
                """
            )
        )
        migrated_session.commit()


def test_a_running_run_must_not_have_a_completion_time(
    migrated_session: Session,
) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO league_season_ingestions (
                    season, status, expected_team_count, successful_team_count,
                    failed_team_count, started_at, completed_at
                ) VALUES (
                    2025, 'RUNNING', 30, 0, 0, '2026-03-01 12:00:00',
                    '2026-03-01 12:05:00'
                )
                """
            )
        )
        migrated_session.commit()


def test_finished_counts_must_add_up(migrated_session: Session) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO league_season_ingestions (
                    season, status, expected_team_count, successful_team_count,
                    failed_team_count, started_at, completed_at
                ) VALUES (
                    2025, 'INCOMPLETE', 30, 20, 1, '2026-03-01 12:00:00',
                    '2026-03-01 12:05:00'
                )
                """
            )
        )
        migrated_session.commit()


def test_an_unknown_status_is_rejected(migrated_session: Session) -> None:
    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                """
                INSERT INTO league_season_ingestions (
                    season, status, expected_team_count, successful_team_count,
                    failed_team_count, started_at, completed_at
                ) VALUES (
                    2025, 'MOSTLY_DONE', 30, 30, 0, '2026-03-01 12:00:00',
                    '2026-03-01 12:05:00'
                )
                """
            )
        )
        migrated_session.commit()


def test_the_domain_state_refuses_a_partial_complete() -> None:
    with pytest.raises(ValueError, match="COMPLETE coverage requires"):
        LeagueSeasonIngestionState(
            season=SEASON,
            status=LeagueSeasonIngestionStatus.COMPLETE,
            expected_team_count=30,
            successful_team_count=29,
            failed_team_count=1,
            started_at=STARTED,
            completed_at=FINISHED,
        )


def test_the_domain_state_refuses_a_finished_run_without_a_completion_time() -> None:
    with pytest.raises(ValueError, match="completed_at"):
        LeagueSeasonIngestionState(
            season=SEASON,
            status=LeagueSeasonIngestionStatus.INCOMPLETE,
            expected_team_count=30,
            successful_team_count=29,
            failed_team_count=1,
            started_at=STARTED,
            completed_at=None,
        )


def test_the_domain_state_refuses_counts_that_do_not_add_up() -> None:
    with pytest.raises(ValueError, match="must equal successful \\+ failed"):
        LeagueSeasonIngestionState(
            season=SEASON,
            status=LeagueSeasonIngestionStatus.INCOMPLETE,
            expected_team_count=30,
            successful_team_count=20,
            failed_team_count=1,
            started_at=STARTED,
            completed_at=FINISHED,
        )
