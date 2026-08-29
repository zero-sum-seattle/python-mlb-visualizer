"""Tests for the bounded-concurrency league ingestion path.

These follow the same shape as ``test_league_season_ingestion.py``: real-path
tests drive discovery, fetch, and persistence together against the captured
fixtures; a few orchestration tests isolate the concurrency and write-ordering
guarantees that fixtures alone cannot exercise on demand. Nothing here touches
the network.
"""

import asyncio
from typing import Any

import pytest
from mlbstatsapi.exceptions import MlbTransportError
from mlbstatsapi.models.schedules import Schedule
from mlbstatsapi.models.teams import Team
from sqlalchemy.orm import Session

from app.database.models import TeamGameBattingLineRecord
from app.database.repositories import get_league_season_ingestion, list_team_season
from app.schemas.ingestion import (
    LeagueSeasonIngestionStatus,
    LeagueTeamIngestionStatus,
)
from app.services.league_season_ingestion import (
    DEFAULT_LEAGUE_CONCURRENCY,
    InvalidConcurrencyError,
    ingest_league_season,
    ingest_league_season_async,
)
from app.services.league_teams import MlbTeamDiscoveryError, NoMlbTeamsDiscoveredError
from tests.test_league_season_ingestion import (
    CUBS_GAME_COUNT,
    CUBS_ID,
    CUBS_NAME,
    MARINERS_ID,
    MARINERS_NAME,
    SEASON,
    build_source,
    make_team,
    stored_row_count,
)


class AsyncFakeLeagueMlb:
    """Async counterpart of ``FakeLeagueMlb``.

    Tracks how many requests of each kind are in flight at once, so a test
    can assert the concurrency bound was actually honored rather than just
    that the final result looks right. ``delay`` forces requests to overlap;
    without it, requests can complete before any other request starts and the
    bound is never actually exercised.
    """

    def __init__(
        self,
        *,
        teams: list[Team] | Exception,
        sources: dict[int, Any] | None = None,
        delay: float = 0.01,
    ) -> None:
        self._teams = teams
        self._sources = sources or {}
        self._delay = delay
        self.team_stats_calls: list[int] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._client_constructions = 0

    @staticmethod
    def _resolve(value: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return value

    async def _tracked_delay(self) -> None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
        finally:
            self.in_flight -= 1

    async def get_teams(self, sport_id: int = 1, **params: Any) -> list[Team]:
        return self._resolve(self._teams)

    async def get_team(self, team_id: int, **params: Any) -> Team | None:
        await self._tracked_delay()
        return self._resolve(self._sources[team_id].team)

    async def get_team_stats(
        self, team_id: int, stats: list[str], groups: list[str], **params: Any
    ) -> dict[str, Any]:
        self.team_stats_calls.append(team_id)
        await self._tracked_delay()
        resolved = self._resolve(self._sources[team_id].team_stats)
        if not isinstance(resolved, dict):
            return resolved
        return {group: resolved[group] for group in groups if group in resolved}

    async def get_schedule(self, **params: Any) -> Schedule | None:
        await self._tracked_delay()
        return self._resolve(self._sources[params["team_id"]].schedule)


def make_async_league_client(
    *, mariners_stats: dict[str, Any] | Exception | None = None, delay: float = 0.01
) -> AsyncFakeLeagueMlb:
    return AsyncFakeLeagueMlb(
        teams=[
            make_team(CUBS_ID, CUBS_NAME),
            make_team(MARINERS_ID, MARINERS_NAME),
        ],
        sources={
            CUBS_ID: build_source(CUBS_ID, CUBS_NAME),
            MARINERS_ID: build_source(
                MARINERS_ID, MARINERS_NAME, team_stats=mariners_stats
            ),
        },
        delay=delay,
    )


def run_async(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Real path: discovery, async fetch, and real persistence
# --------------------------------------------------------------------------


def test_every_discovered_team_is_ingested(migrated_session: Session) -> None:
    result = run_async(
        ingest_league_season_async(
            session=migrated_session, season=SEASON, client=make_async_league_client()
        )
    )
    assert result.teams_discovered == 2
    assert result.teams_succeeded == 2
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE


def test_both_clubs_games_are_actually_persisted(migrated_session: Session) -> None:
    run_async(
        ingest_league_season_async(
            session=migrated_session, season=SEASON, client=make_async_league_client()
        )
    )
    cubs = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    mariners = list_team_season(migrated_session, team_id=MARINERS_ID, season=SEASON)
    assert len(cubs) == CUBS_GAME_COUNT
    assert len(mariners) == CUBS_GAME_COUNT
    assert stored_row_count(migrated_session) == 2 * CUBS_GAME_COUNT


def test_repeat_ingestion_is_idempotent(migrated_session: Session) -> None:
    run_async(
        ingest_league_season_async(
            session=migrated_session, season=SEASON, client=make_async_league_client()
        )
    )
    result = run_async(
        ingest_league_season_async(
            session=migrated_session, season=SEASON, client=make_async_league_client()
        )
    )
    assert (result.inserted, result.updated) == (0, 0)
    assert result.unchanged == 2 * CUBS_GAME_COUNT
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE


def test_a_failing_club_does_not_undo_the_clubs_before_it(
    migrated_session: Session,
) -> None:
    result = run_async(
        ingest_league_season_async(
            session=migrated_session,
            season=SEASON,
            client=make_async_league_client(
                mariners_stats=MlbTransportError("Request failed")
            ),
        )
    )
    assert result.teams_succeeded == 1
    assert result.teams_failed == 1
    assert result.status is LeagueSeasonIngestionStatus.INCOMPLETE
    assert len(list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)) == (
        CUBS_GAME_COUNT
    )


def test_a_rerun_can_reach_complete_after_a_failure(migrated_session: Session) -> None:
    run_async(
        ingest_league_season_async(
            session=migrated_session,
            season=SEASON,
            client=make_async_league_client(
                mariners_stats=MlbTransportError("Request failed")
            ),
        )
    )
    result = run_async(
        ingest_league_season_async(
            session=migrated_session, season=SEASON, client=make_async_league_client()
        )
    )
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.COMPLETE


def test_failed_team_result_names_the_club_and_the_error(
    migrated_session: Session,
) -> None:
    result = run_async(
        ingest_league_season_async(
            session=migrated_session,
            season=SEASON,
            client=make_async_league_client(
                mariners_stats=MlbTransportError("Request failed")
            ),
        )
    )
    failed = next(
        team
        for team in result.team_results
        if team.status is LeagueTeamIngestionStatus.FAILED
    )
    assert (failed.team_id, failed.team_name) == (MARINERS_ID, MARINERS_NAME)
    assert "TeamGameLogError" in (failed.error or "")


def test_coverage_state_is_persisted(migrated_session: Session) -> None:
    result = run_async(
        ingest_league_season_async(
            session=migrated_session, season=SEASON, client=make_async_league_client()
        )
    )
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.COMPLETE
    assert state.started_at == result.started_at
    assert state.completed_at == result.completed_at


# --------------------------------------------------------------------------
# Sequential vs concurrent parity
# --------------------------------------------------------------------------


def test_async_and_sequential_persist_the_same_rows(migrated_session: Session) -> None:
    from tests.test_league_season_ingestion import make_league_client

    run_async(
        ingest_league_season_async(
            session=migrated_session, season=SEASON, client=make_async_league_client()
        )
    )
    async_rows = {
        (line.team_id, line.game_pk, line.hits, line.runs)
        for line in list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    } | {
        (line.team_id, line.game_pk, line.hits, line.runs)
        for line in list_team_season(
            migrated_session, team_id=MARINERS_ID, season=SEASON
        )
    }

    migrated_session.query(TeamGameBattingLineRecord).delete()
    migrated_session.commit()

    ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    sync_rows = {
        (line.team_id, line.game_pk, line.hits, line.runs)
        for line in list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    } | {
        (line.team_id, line.game_pk, line.hits, line.runs)
        for line in list_team_season(
            migrated_session, team_id=MARINERS_ID, season=SEASON
        )
    }

    assert async_rows == sync_rows


# --------------------------------------------------------------------------
# Concurrency bound, client reuse, and write serialization
# --------------------------------------------------------------------------


SYNTHETIC_TEAM_ID_BASE = 900


def many_teams_client(count: int, *, delay: float = 0.01) -> AsyncFakeLeagueMlb:
    """Build ``count`` synthetic clubs, each a retargeted copy of the Cubs.

    Ids start well above any real MLB team id (roughly 108-158) so a
    synthetic club never collides with an opponent already present inside
    the retargeted Cubs fixture data, the way ``100 + i`` did (108 collides
    with the Angels, who the fixture Cubs season actually played).
    """
    ids = [SYNTHETIC_TEAM_ID_BASE + i for i in range(count)]
    teams = [make_team(team_id, f"Team {team_id}") for team_id in ids]
    sources = {team_id: build_source(team_id, f"Team {team_id}") for team_id in ids}
    return AsyncFakeLeagueMlb(teams=teams, sources=sources, delay=delay)


def test_concurrent_fetches_never_exceed_the_bound(migrated_session: Session) -> None:
    client = many_teams_client(6, delay=0.02)
    run_async(
        ingest_league_season_async(
            session=migrated_session, season=SEASON, client=client, concurrency=2
        )
    )
    assert client.max_in_flight <= 2
    # The bound must actually have been exercised, not just never violated.
    assert client.max_in_flight >= 2


def test_default_concurrency_is_a_modest_positive_bound() -> None:
    assert DEFAULT_LEAGUE_CONCURRENCY >= 1


@pytest.mark.parametrize("concurrency", [0, -1])
def test_an_invalid_concurrency_is_refused_before_any_request(
    migrated_session: Session, concurrency: int
) -> None:
    client = many_teams_client(2)
    with pytest.raises(InvalidConcurrencyError):
        run_async(
            ingest_league_season_async(
                session=migrated_session,
                season=SEASON,
                client=client,
                concurrency=concurrency,
            )
        )
    assert client.team_stats_calls == []


def test_one_client_is_opened_and_closed_for_the_whole_run(
    migrated_session: Session,
) -> None:
    """Thirty teams must not mean thirty AsyncMlb clients."""
    from unittest.mock import patch

    owned = make_async_league_client()
    closed: list[bool] = []

    class OwnedClient:
        async def __aenter__(self) -> AsyncFakeLeagueMlb:
            return owned

        async def __aexit__(self, *args: object) -> None:
            closed.append(True)

    with patch(
        "app.services.league_season_ingestion.AsyncMlb", return_value=OwnedClient()
    ) as client_factory:
        result = run_async(
            ingest_league_season_async(session=migrated_session, season=SEASON)
        )

    assert client_factory.call_count == 1
    assert closed == [True]
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE


def test_no_overlapping_database_writes(migrated_session: Session) -> None:
    """Persistence must never run for two teams at the same time.

    ``persist_team_season`` itself contains no ``await``, so nothing can
    interleave with it once it starts; this guards the ``write_lock`` that
    makes that guarantee explicit rather than incidental, in case a future
    change adds an ``await`` to the persistence path.
    """
    from unittest.mock import patch

    import app.services.league_season_ingestion as league_module

    active_writers = 0
    max_active_writers = 0
    real_persist = league_module.persist_team_season

    def tracking_persist(*args: object, **kwargs: object):
        nonlocal active_writers, max_active_writers
        active_writers += 1
        max_active_writers = max(max_active_writers, active_writers)
        try:
            return real_persist(*args, **kwargs)
        finally:
            active_writers -= 1

    client = many_teams_client(6, delay=0.02)
    with patch(
        "app.services.league_season_ingestion.persist_team_season",
        side_effect=tracking_persist,
    ):
        result = run_async(
            ingest_league_season_async(
                session=migrated_session, season=SEASON, client=client, concurrency=4
            )
        )

    assert max_active_writers == 1
    assert result.teams_succeeded == 6


def test_an_unexpected_error_is_not_reported_as_a_missing_team(
    migrated_session: Session,
) -> None:
    from unittest.mock import patch

    with (
        patch(
            "app.services.league_season_ingestion.get_team_game_lines_async",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        run_async(
            ingest_league_season_async(
                session=migrated_session,
                season=SEASON,
                client=make_async_league_client(),
            )
        )


def test_coverage_is_left_running_when_a_run_does_not_finish(
    migrated_session: Session,
) -> None:
    from unittest.mock import patch

    with (
        patch(
            "app.services.league_season_ingestion.get_team_game_lines_async",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError),
    ):
        run_async(
            ingest_league_season_async(
                session=migrated_session,
                season=SEASON,
                client=make_async_league_client(),
            )
        )
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.RUNNING
    assert state.completed_at is None


def test_zero_discovered_teams_stops_before_any_state_is_written(
    migrated_session: Session,
) -> None:
    client = AsyncFakeLeagueMlb(teams=[])
    with pytest.raises(NoMlbTeamsDiscoveredError):
        run_async(
            ingest_league_season_async(
                session=migrated_session, season=SEASON, client=client
            )
        )
    assert get_league_season_ingestion(migrated_session, season=SEASON) is None


def test_discovery_failure_stops_before_any_state_is_written(
    migrated_session: Session,
) -> None:
    client = AsyncFakeLeagueMlb(teams=MlbTransportError("Request failed"))
    with pytest.raises(MlbTeamDiscoveryError):
        run_async(
            ingest_league_season_async(
                session=migrated_session, season=SEASON, client=client
            )
        )
    assert get_league_season_ingestion(migrated_session, season=SEASON) is None


def test_progress_position_is_within_range_and_teams_all_report(
    migrated_session: Session,
) -> None:
    seen: list[tuple[int, int, int]] = []
    run_async(
        ingest_league_season_async(
            session=migrated_session,
            season=SEASON,
            client=make_async_league_client(),
            on_team_complete=lambda position, total, result: seen.append(
                (position, total, result.team_id)
            ),
        )
    )
    assert len(seen) == 2
    assert {position for position, _, _ in seen} == {1, 2}
    assert {total for _, total, _ in seen} == {2}
    assert {team_id for _, _, team_id in seen} == {CUBS_ID, MARINERS_ID}


def test_aggregate_row_count_at_scale_matches_the_sequential_path(
    migrated_session: Session,
) -> None:
    client = many_teams_client(10, delay=0.005)
    result = run_async(
        ingest_league_season_async(
            session=migrated_session, season=SEASON, client=client, concurrency=3
        )
    )
    assert result.teams_discovered == 10
    assert result.teams_succeeded == 10
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE
    assert stored_row_count(migrated_session) == 10 * CUBS_GAME_COUNT
