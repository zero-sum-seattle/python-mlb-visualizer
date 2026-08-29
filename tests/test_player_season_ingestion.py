"""Tests for the player-season ingestion service."""

from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.models import PlayerRecord, PlayerSeasonHittingRecord
from app.database.repositories import get_player, get_player_season_hitting
from app.schemas.ingestion import PlayerPersistenceOutcome
from app.services.player_season_ingestion import (
    PlayerSeasonIngestionError,
    ingest_player_season,
)
from app.services.players import NoHittingStatsError, PlayerNotFoundError
from tests.test_players_service import FakeMlb, make_person, make_split, make_stat

PLAYER_ID = 677594
SEASON = 2025
MARINERS_STATS = {"hitting": {"season": make_stat([make_split()])}}


def make_client() -> FakeMlb:
    return FakeMlb(person=make_person(), player_stats=MARINERS_STATS)


def test_first_import_inserts_both_rows(migrated_session: Session) -> None:
    result = ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=make_client(),
    )
    assert result.identity_outcome is PlayerPersistenceOutcome.INSERTED
    assert result.hitting_outcome is PlayerPersistenceOutcome.INSERTED
    assert result.full_name == "Julio Rodriguez"
    assert get_player(migrated_session, player_id=PLAYER_ID) is not None
    assert (
        get_player_season_hitting(migrated_session, player_id=PLAYER_ID, season=SEASON)
        is not None
    )


def test_second_identical_import_reports_unchanged(migrated_session: Session) -> None:
    ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=make_client(),
    )
    result = ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=make_client(),
    )
    assert result.identity_outcome is PlayerPersistenceOutcome.UNCHANGED
    assert result.hitting_outcome is PlayerPersistenceOutcome.UNCHANGED

    player_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerRecord)
    )
    hitting_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerSeasonHittingRecord)
    )
    assert player_count == 1
    assert hitting_count == 1


def test_changed_season_stats_update_without_duplicate(
    migrated_session: Session,
) -> None:
    ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=make_client(),
    )
    changed_stats = {"hitting": {"season": make_stat([make_split(hits=200)])}}
    result = ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=FakeMlb(person=make_person(), player_stats=changed_stats),
    )
    assert result.hitting_outcome is PlayerPersistenceOutcome.UPDATED
    hitting_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerSeasonHittingRecord)
    )
    assert hitting_count == 1
    stored = get_player_season_hitting(
        migrated_session, player_id=PLAYER_ID, season=SEASON
    )
    assert stored.hits == 200


def test_changed_identity_updates_without_duplicate(migrated_session: Session) -> None:
    ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=make_client(),
    )
    renamed_client = FakeMlb(
        person=make_person(full_name="J-Rod"), player_stats=MARINERS_STATS
    )
    result = ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=renamed_client,
    )
    assert result.identity_outcome is PlayerPersistenceOutcome.UPDATED
    player_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerRecord)
    )
    assert player_count == 1
    assert get_player(migrated_session, player_id=PLAYER_ID).full_name == "J-Rod"


def test_in_progress_season_totals_increase_and_stay_idempotent(
    migrated_session: Session,
) -> None:
    """A season whose totals grow over time never produces duplicate rows."""
    early_stats = {"hitting": {"season": make_stat([make_split(games_played=50)])}}
    ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=FakeMlb(person=make_person(), player_stats=early_stats),
    )
    later_stats = {"hitting": {"season": make_stat([make_split(games_played=100)])}}
    first_rerun = ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=FakeMlb(person=make_person(), player_stats=later_stats),
    )
    assert first_rerun.hitting_outcome is PlayerPersistenceOutcome.UPDATED

    second_rerun = ingest_player_season(
        session=migrated_session,
        player_id=PLAYER_ID,
        season=SEASON,
        client=FakeMlb(person=make_person(), player_stats=later_stats),
    )
    assert second_rerun.hitting_outcome is PlayerPersistenceOutcome.UNCHANGED

    hitting_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerSeasonHittingRecord)
    )
    assert hitting_count == 1


def test_player_not_found_leaves_database_unchanged(migrated_session: Session) -> None:
    client = FakeMlb(person=None)
    with pytest.raises(PlayerNotFoundError):
        ingest_player_season(
            session=migrated_session, player_id=PLAYER_ID, season=SEASON, client=client
        )
    player_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerRecord)
    )
    assert player_count == 0


def test_no_hitting_stats_leaves_database_unchanged(migrated_session: Session) -> None:
    client = FakeMlb(person=make_person(), player_stats={})
    with pytest.raises(NoHittingStatsError):
        ingest_player_season(
            session=migrated_session, player_id=PLAYER_ID, season=SEASON, client=client
        )
    player_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerRecord)
    )
    hitting_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerSeasonHittingRecord)
    )
    assert player_count == 0
    assert hitting_count == 0


def test_database_error_rolls_back_both_writes(migrated_session: Session) -> None:
    """The second persistence operation failing must not leave the first committed."""
    with (
        patch(
            "app.services.player_season_ingestion.upsert_player_season_hitting",
            side_effect=SQLAlchemyError("db failed"),
        ),
        pytest.raises(PlayerSeasonIngestionError),
    ):
        ingest_player_season(
            session=migrated_session,
            player_id=PLAYER_ID,
            season=SEASON,
            client=make_client(),
        )
    player_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerRecord)
    )
    hitting_count = migrated_session.scalar(
        select(func.count()).select_from(PlayerSeasonHittingRecord)
    )
    assert player_count == 0
    assert hitting_count == 0


def test_no_partial_state_after_failed_transaction(migrated_session: Session) -> None:
    """Failing after the player row is staged still rolls both rows back."""

    def failing_hitting_upsert(session: Session, *, hitting: object) -> None:
        raise SQLAlchemyError("fail after player staged")

    with (
        patch(
            "app.services.player_season_ingestion.upsert_player_season_hitting",
            side_effect=failing_hitting_upsert,
        ),
        pytest.raises(PlayerSeasonIngestionError),
    ):
        ingest_player_season(
            session=migrated_session,
            player_id=PLAYER_ID,
            season=SEASON,
            client=make_client(),
        )
    assert get_player(migrated_session, player_id=PLAYER_ID) is None
    assert (
        get_player_season_hitting(migrated_session, player_id=PLAYER_ID, season=SEASON)
        is None
    )


def test_mlb_retrieval_runs_before_transaction_opens(migrated_session: Session) -> None:
    """Both MLB requests must complete before any DB transaction is opened."""
    fetch_order: list[str] = []

    class TrackingSession:
        def __init__(self, real: Session) -> None:
            self._real = real

        def begin(self) -> object:
            fetch_order.append("transaction_begin")
            return self._real.begin()

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    class TrackingClient(FakeMlb):
        def get_person(self, player_id: int, **params: object) -> object:
            fetch_order.append("get_person")
            return super().get_person(player_id, **params)

        def get_player_stats(
            self, person_id: int, stats: list, groups: list, **params: object
        ) -> dict:
            fetch_order.append("get_player_stats")
            return super().get_player_stats(person_id, stats, groups, **params)

    client = TrackingClient(person=make_person(), player_stats=MARINERS_STATS)
    tracking_session = TrackingSession(migrated_session)

    ingest_player_season(
        session=tracking_session,  # type: ignore[arg-type]
        player_id=PLAYER_ID,
        season=SEASON,
        client=client,
    )

    assert fetch_order == ["get_person", "get_player_stats", "transaction_begin"]
