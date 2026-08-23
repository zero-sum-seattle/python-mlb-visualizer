"""Tests for the league-wide season ingestion service.

Two kinds of test live here.

The *real path* tests drive the whole chain: discovery, the existing
``ingest_team_season``, the existing normalization and upsert, a real migrated
SQLite database, and the persisted coverage row. They use the captured 2025 Cubs
fixtures, retargeted to a second club so a league can hold more than one team
without inventing new baseball data.

The *orchestration* tests replace ``ingest_team_season`` at the league service's
own boundary to drive outcomes that fixtures cannot produce on demand, such as a
specific club failing. They assert the league service calls that exact function,
which is what makes them evidence of reuse rather than of a second
implementation.

Nothing here touches the network.
"""

import copy
from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest
from mlbstatsapi.exceptions import MlbTransportError
from mlbstatsapi.models.schedules import Schedule
from mlbstatsapi.models.teams import Team
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import TeamGameBattingLineRecord
from app.database.repositories import get_league_season_ingestion, list_team_season
from app.schemas.ingestion import (
    LeagueSeasonIngestionStatus,
    LeagueTeamIngestionStatus,
    TeamSeasonIngestionResult,
)
from app.schemas.teams import MlbTeam
from app.services.league_season_ingestion import (
    InvalidSeasonError,
    ingest_league_season,
)
from app.services.league_teams import (
    MlbTeamDiscoveryError,
    NoMlbTeamsDiscoveredError,
)
from app.services.team_game_logs import TeamGameDataError, TeamNotFoundError
from app.services.team_season_ingestion import TeamSeasonIngestionError
from tests.test_league_teams import MLB_SPORT, make_team
from tests.test_team_game_logs import (
    build_schedule,
    build_team_stats,
    drop_game_log_splits,
    load_payload,
)

SEASON = 2025
CUBS_ID = 112
CUBS_NAME = "Chicago Cubs"
# The second club in these tests. Reusing a real id and name keeps the fixtures
# readable; the games themselves are the Cubs' games retargeted, so this club's
# batting lines are not claimed to be real Mariners history anywhere outside
# these tests.
MARINERS_ID = 136
MARINERS_NAME = "Seattle Mariners"
CUBS_GAME_COUNT = 6


def retarget(payload: dict[str, Any], team_id: int, team_name: str) -> dict[str, Any]:
    """Copy a captured Cubs payload with the Cubs replaced by another club.

    Game ids are left alone on purpose: in a real league-wide import both clubs
    in a game are stored under the same ``game_pk``, so keeping it exercises
    that rather than hiding it.
    """

    def rewrite(node: Any) -> Any:
        if isinstance(node, dict):
            rewritten = {key: rewrite(value) for key, value in node.items()}
            if rewritten.get("id") == CUBS_ID:
                rewritten["id"] = team_id
                rewritten["link"] = f"/api/v1/teams/{team_id}"
                if "name" in rewritten:
                    rewritten["name"] = team_name
            return rewritten
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        return node

    return rewrite(copy.deepcopy(payload))


class TeamSource:
    """Everything the game-log service asks about one club."""

    def __init__(
        self,
        *,
        team: Team,
        team_stats: dict[str, Any] | Exception,
        schedule: Schedule | Exception,
    ) -> None:
        self.team = team
        self.team_stats = team_stats
        self.schedule = schedule


def build_source(
    team_id: int,
    team_name: str,
    *,
    team_stats: dict[str, Any] | Exception | None = None,
    schedule: Schedule | Exception | None = None,
) -> TeamSource:
    """Build one club's fixture data, retargeted from the captured Cubs season."""
    game_log = retarget(load_payload("cubs_2025_hitting_game_log"), team_id, team_name)
    pitching_log = retarget(
        load_payload("cubs_2025_pitching_game_log"), team_id, team_name
    )
    raw_schedule = retarget(load_payload("cubs_2025_schedule"), team_id, team_name)
    # Both stat groups in one payload, the way a club that has played has both.
    both_groups = {"stats": [*game_log["stats"], *pitching_log["stats"]]}
    return TeamSource(
        team=Team(
            id=team_id,
            link=f"/api/v1/teams/{team_id}",
            name=team_name,
            sport=MLB_SPORT,
        ),
        team_stats=(
            team_stats if team_stats is not None else build_team_stats(both_groups)
        ),
        schedule=schedule if schedule is not None else build_schedule(raw_schedule),
    )


class FakeLeagueMlb:
    """Stands in for ``mlbstatsapi.Mlb`` for discovery and every team fetch."""

    def __init__(
        self,
        *,
        teams: list[Team] | Exception,
        sources: dict[int, TeamSource] | None = None,
    ) -> None:
        self._teams = teams
        self._sources = sources or {}
        self.team_stats_calls: list[int] = []

    @staticmethod
    def _resolve(value: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return value

    def get_teams(self, sport_id: int = 1, **params: Any) -> list[Team]:
        return self._resolve(self._teams)

    def get_team(self, team_id: int, **params: Any) -> Team | None:
        return self._resolve(self._sources[team_id].team)

    def get_team_stats(
        self,
        team_id: int,
        stats: list[str],
        groups: list[str],
        **params: Any,
    ) -> dict[str, Any]:
        self.team_stats_calls.append(team_id)
        resolved = self._resolve(self._sources[team_id].team_stats)
        if not isinstance(resolved, dict):
            return resolved
        # The real client returns only the groups asked for.
        return {group: resolved[group] for group in groups if group in resolved}

    def get_schedule(self, **params: Any) -> Schedule | None:
        return self._resolve(self._sources[params["team_id"]].schedule)


def make_league_client(
    *,
    mariners_stats: dict[str, Any] | Exception | None = None,
) -> FakeLeagueMlb:
    """A two-club 2025 league backed by the captured fixtures."""
    return FakeLeagueMlb(
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
    )


def stored_row_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TeamGameBattingLineRecord))


# --------------------------------------------------------------------------
# Real path: discovery, the existing team ingestion, and real persistence
# --------------------------------------------------------------------------


def test_every_discovered_team_is_ingested(migrated_session: Session) -> None:
    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    assert result.teams_discovered == 2
    assert result.teams_succeeded == 2
    assert result.teams_failed == 0
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE


def test_aggregate_counts_sum_the_per_team_counts(migrated_session: Session) -> None:
    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    assert result.team_game_records_fetched == 2 * CUBS_GAME_COUNT
    assert result.inserted == 2 * CUBS_GAME_COUNT
    assert result.updated == 0
    assert result.unchanged == 0


def test_per_team_results_keep_team_identity(migrated_session: Session) -> None:
    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    assert [(team.team_id, team.team_name) for team in result.team_results] == [
        (CUBS_ID, CUBS_NAME),
        (MARINERS_ID, MARINERS_NAME),
    ]
    assert {team.season for team in result.team_results} == {SEASON}


def test_both_clubs_games_are_actually_persisted(migrated_session: Session) -> None:
    ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    cubs = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    mariners = list_team_season(migrated_session, team_id=MARINERS_ID, season=SEASON)
    assert len(cubs) == CUBS_GAME_COUNT
    assert len(mariners) == CUBS_GAME_COUNT
    assert stored_row_count(migrated_session) == 2 * CUBS_GAME_COUNT


def test_two_clubs_can_share_a_game_pk(migrated_session: Session) -> None:
    """One MLB game becomes one team-game record per club, not one row total."""
    ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    cubs = {
        line.game_pk
        for line in list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    }
    mariners = {
        line.game_pk
        for line in list_team_season(
            migrated_session, team_id=MARINERS_ID, season=SEASON
        )
    }
    assert cubs == mariners
    assert stored_row_count(migrated_session) == 2 * len(cubs)


def test_repeat_ingestion_is_idempotent(migrated_session: Session) -> None:
    ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    assert (result.inserted, result.updated) == (0, 0)
    assert result.unchanged == 2 * CUBS_GAME_COUNT
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE
    assert stored_row_count(migrated_session) == 2 * CUBS_GAME_COUNT


def test_a_failing_club_does_not_undo_the_clubs_before_it(
    migrated_session: Session,
) -> None:
    result = ingest_league_season(
        session=migrated_session,
        season=SEASON,
        client=make_league_client(mariners_stats=MlbTransportError("Request failed")),
    )
    assert result.teams_succeeded == 1
    assert result.teams_failed == 1
    assert result.status is LeagueSeasonIngestionStatus.INCOMPLETE
    assert len(list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)) == (
        CUBS_GAME_COUNT
    )
    assert stored_row_count(migrated_session) == CUBS_GAME_COUNT


def test_a_rerun_can_reach_complete_after_a_failure(
    migrated_session: Session,
) -> None:
    ingest_league_season(
        session=migrated_session,
        season=SEASON,
        client=make_league_client(mariners_stats=MlbTransportError("Request failed")),
    )
    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE
    assert result.unchanged == CUBS_GAME_COUNT
    assert result.inserted == CUBS_GAME_COUNT
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.COMPLETE


def test_failed_team_result_names_the_club_and_the_error(
    migrated_session: Session,
) -> None:
    result = ingest_league_season(
        session=migrated_session,
        season=SEASON,
        client=make_league_client(mariners_stats=MlbTransportError("Request failed")),
    )
    failed = next(
        team
        for team in result.team_results
        if team.status is LeagueTeamIngestionStatus.FAILED
    )
    assert (failed.team_id, failed.team_name) == (MARINERS_ID, MARINERS_NAME)
    assert "TeamGameLogError" in (failed.error or "")
    assert (failed.fetched, failed.inserted, failed.updated) == (0, 0, 0)


def test_coverage_state_is_persisted_for_the_season(
    migrated_session: Session,
) -> None:
    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.COMPLETE
    assert state.expected_team_count == 2
    assert state.successful_team_count == 2
    assert state.failed_team_count == 0
    assert state.started_at == result.started_at
    assert state.completed_at == result.completed_at


def test_incomplete_coverage_is_persisted_with_its_counts(
    migrated_session: Session,
) -> None:
    ingest_league_season(
        session=migrated_session,
        season=SEASON,
        client=make_league_client(mariners_stats=MlbTransportError("Request failed")),
    )
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.INCOMPLETE
    assert (state.expected_team_count, state.successful_team_count) == (2, 1)
    assert state.failed_team_count == 1


# --------------------------------------------------------------------------
# Discovery and input failures
# --------------------------------------------------------------------------


def test_zero_discovered_teams_stops_before_any_state_is_written(
    migrated_session: Session,
) -> None:
    """Nothing was attempted, so nothing should claim to have been covered."""
    client = FakeLeagueMlb(teams=[])
    with pytest.raises(NoMlbTeamsDiscoveredError):
        ingest_league_season(session=migrated_session, season=SEASON, client=client)
    assert get_league_season_ingestion(migrated_session, season=SEASON) is None


def test_discovery_failure_stops_before_any_state_is_written(
    migrated_session: Session,
) -> None:
    client = FakeLeagueMlb(teams=MlbTransportError("Request failed"))
    with pytest.raises(MlbTeamDiscoveryError):
        ingest_league_season(session=migrated_session, season=SEASON, client=client)
    assert get_league_season_ingestion(migrated_session, season=SEASON) is None


@pytest.mark.parametrize("season", [0, -1, 1875, 99999])
def test_an_impossible_season_is_refused_before_any_request(
    migrated_session: Session, season: int
) -> None:
    client = FakeLeagueMlb(teams=[])
    with pytest.raises(InvalidSeasonError):
        ingest_league_season(session=migrated_session, season=season, client=client)
    assert client.team_stats_calls == []


def test_next_season_is_allowed(migrated_session: Session) -> None:
    """A schedule is published before the season starts, so it can be ingested."""
    next_season = datetime.now().year + 1
    client = FakeLeagueMlb(teams=[make_team(CUBS_ID, CUBS_NAME, season=next_season)])
    with patch(
        "app.services.league_season_ingestion.ingest_team_season",
        return_value=TeamSeasonIngestionResult(
            team_id=CUBS_ID,
            team_name=CUBS_NAME,
            season=next_season,
            fetched=0,
            inserted=0,
            updated=0,
            unchanged=0,
        ),
    ):
        result = ingest_league_season(
            session=migrated_session, season=next_season, client=client
        )
    assert result.season == next_season


# --------------------------------------------------------------------------
# Orchestration: reuse of the existing team-season ingestion service
# --------------------------------------------------------------------------


def team_result(team: MlbTeam, **counts: int) -> TeamSeasonIngestionResult:
    fetched = counts.get("inserted", 0) + counts.get("updated", 0)
    fetched += counts.get("unchanged", 0)
    return TeamSeasonIngestionResult(
        team_id=team.team_id,
        team_name=team.team_name,
        season=team.season,
        fetched=fetched,
        inserted=counts.get("inserted", 0),
        updated=counts.get("updated", 0),
        unchanged=counts.get("unchanged", 0),
    )


def three_team_client() -> FakeLeagueMlb:
    return FakeLeagueMlb(
        teams=[
            make_team(108, "Los Angeles Angels"),
            make_team(CUBS_ID, CUBS_NAME),
            make_team(MARINERS_ID, MARINERS_NAME),
        ]
    )


def test_the_existing_team_ingestion_service_is_the_one_called(
    migrated_session: Session,
) -> None:
    client = make_league_client()
    with patch(
        "app.services.league_season_ingestion.ingest_team_season",
        side_effect=lambda **kwargs: team_result(
            MlbTeam(
                team_id=kwargs["team_id"],
                team_name=f"team {kwargs['team_id']}",
                season=kwargs["season"],
            ),
            inserted=1,
        ),
    ) as ingest:
        ingest_league_season(session=migrated_session, season=SEASON, client=client)

    assert [call.kwargs["team_id"] for call in ingest.call_args_list] == [
        CUBS_ID,
        MARINERS_ID,
    ]
    for call in ingest.call_args_list:
        assert call.kwargs["session"] is migrated_session
        assert call.kwargs["season"] == SEASON
        assert call.kwargs["client"] is client


def test_teams_are_ingested_sequentially_in_discovery_order(
    migrated_session: Session,
) -> None:
    visited: list[int] = []

    def record(**kwargs: Any) -> TeamSeasonIngestionResult:
        visited.append(kwargs["team_id"])
        return team_result(
            MlbTeam(
                team_id=kwargs["team_id"],
                team_name=f"team {kwargs['team_id']}",
                season=kwargs["season"],
            ),
            unchanged=2,
        )

    with patch(
        "app.services.league_season_ingestion.ingest_team_season", side_effect=record
    ):
        result = ingest_league_season(
            session=migrated_session, season=SEASON, client=three_team_client()
        )
    # Discovery sorts by club name: Chicago, Los Angeles, Seattle.
    assert visited == [CUBS_ID, 108, MARINERS_ID]
    assert [team.team_id for team in result.team_results] == visited


@pytest.mark.parametrize(
    "error",
    [
        TeamNotFoundError("no such team"),
        TeamGameDataError("contradictory upstream data"),
        TeamSeasonIngestionError("could not persist"),
    ],
)
def test_each_ingestion_failure_mode_becomes_a_failed_team(
    migrated_session: Session, error: Exception
) -> None:
    def fail_the_cubs(**kwargs: Any) -> TeamSeasonIngestionResult:
        if kwargs["team_id"] == CUBS_ID:
            raise error
        return team_result(
            MlbTeam(
                team_id=kwargs["team_id"],
                team_name=f"team {kwargs['team_id']}",
                season=kwargs["season"],
            ),
            inserted=3,
        )

    with patch(
        "app.services.league_season_ingestion.ingest_team_season",
        side_effect=fail_the_cubs,
    ):
        result = ingest_league_season(
            session=migrated_session, season=SEASON, client=three_team_client()
        )

    assert result.teams_succeeded == 2
    assert result.teams_failed == 1
    assert result.status is LeagueSeasonIngestionStatus.INCOMPLETE
    failed = next(
        team
        for team in result.team_results
        if team.status is LeagueTeamIngestionStatus.FAILED
    )
    assert failed.team_id == CUBS_ID
    assert type(error).__name__ in (failed.error or "")


def test_several_failures_are_all_recorded(migrated_session: Session) -> None:
    def fail_all_but_one(**kwargs: Any) -> TeamSeasonIngestionResult:
        if kwargs["team_id"] != 108:
            raise TeamGameDataError(f"team {kwargs['team_id']} is unusable")
        return team_result(
            MlbTeam(team_id=108, team_name="Los Angeles Angels", season=SEASON),
            inserted=5,
        )

    with patch(
        "app.services.league_season_ingestion.ingest_team_season",
        side_effect=fail_all_but_one,
    ):
        result = ingest_league_season(
            session=migrated_session, season=SEASON, client=three_team_client()
        )

    assert (result.teams_discovered, result.teams_succeeded) == (3, 1)
    assert result.teams_failed == 2
    assert result.team_game_records_fetched == 5
    assert result.status is LeagueSeasonIngestionStatus.INCOMPLETE
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert (state.successful_team_count, state.failed_team_count) == (1, 2)


def test_an_unexpected_error_is_not_reported_as_a_missing_team(
    migrated_session: Session,
) -> None:
    """Only ingestion failures are absorbed; a bug must not look like a bad club."""
    with (
        patch(
            "app.services.league_season_ingestion.ingest_team_season",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        ingest_league_season(
            session=migrated_session, season=SEASON, client=three_team_client()
        )


def test_coverage_is_left_running_when_a_run_does_not_finish(
    migrated_session: Session,
) -> None:
    """A crashed run must not leave a stale COMPLETE behind for the season."""
    with (
        patch(
            "app.services.league_season_ingestion.ingest_team_season",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError),
    ):
        ingest_league_season(
            session=migrated_session, season=SEASON, client=three_team_client()
        )
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.RUNNING
    assert state.completed_at is None


def test_a_later_run_replaces_a_stale_running_row(migrated_session: Session) -> None:
    with (
        patch(
            "app.services.league_season_ingestion.ingest_team_season",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError),
    ):
        ingest_league_season(
            session=migrated_session, season=SEASON, client=three_team_client()
        )
    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.COMPLETE


def test_progress_is_reported_as_each_team_finishes(
    migrated_session: Session,
) -> None:
    seen: list[tuple[int, int, int]] = []
    ingest_league_season(
        session=migrated_session,
        season=SEASON,
        client=make_league_client(),
        on_team_complete=lambda position, total, result: seen.append(
            (position, total, result.team_id)
        ),
    )
    assert seen == [(1, 2, CUBS_ID), (2, 2, MARINERS_ID)]


# --------------------------------------------------------------------------
# Client ownership, session hygiene, and coverage-write failures
# --------------------------------------------------------------------------


def test_one_client_is_opened_and_closed_for_the_whole_run(
    migrated_session: Session,
) -> None:
    """Thirty teams must not mean thirty MLB clients."""
    owned = make_league_client()
    closed: list[bool] = []

    class OwnedClient:
        def __enter__(self) -> FakeLeagueMlb:
            return owned

        def __exit__(self, *args: object) -> None:
            closed.append(True)

    with patch(
        "app.services.league_season_ingestion.Mlb", return_value=OwnedClient()
    ) as client_factory:
        result = ingest_league_season(session=migrated_session, season=SEASON)

    assert client_factory.call_count == 1
    assert closed == [True]
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE


def test_a_team_failure_leaves_the_session_usable(migrated_session: Session) -> None:
    """A stray open transaction would make the next team's commit impossible."""

    def fail_first_with_an_open_transaction(**kwargs: Any) -> TeamSeasonIngestionResult:
        if kwargs["team_id"] == CUBS_ID:
            kwargs["session"].execute(
                select(func.count()).select_from(TeamGameBattingLineRecord)
            )
            raise TeamGameDataError("upstream data is unusable")
        return team_result(
            MlbTeam(
                team_id=kwargs["team_id"],
                team_name=f"team {kwargs['team_id']}",
                season=kwargs["season"],
            ),
            inserted=4,
        )

    with patch(
        "app.services.league_season_ingestion.ingest_team_season",
        side_effect=fail_first_with_an_open_transaction,
    ):
        result = ingest_league_season(
            session=migrated_session, season=SEASON, client=three_team_client()
        )

    assert (result.teams_succeeded, result.teams_failed) == (2, 1)
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.INCOMPLETE


def test_a_failed_coverage_write_is_reported_with_its_cause(
    migrated_session: Session,
) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.league_season_ingestion import LeagueIngestionStateError

    cause = SQLAlchemyError("database is locked")
    with (
        patch(
            "app.services.league_season_ingestion.record_league_season_ingestion_start",
            side_effect=cause,
        ),
        pytest.raises(LeagueIngestionStateError, match="season 2025") as exc_info,
    ):
        ingest_league_season(
            session=migrated_session, season=SEASON, client=make_league_client()
        )
    assert exc_info.value.__cause__ is cause


# --------------------------------------------------------------------------
# A team-season refused for incompleteness must not abort the league
# --------------------------------------------------------------------------

NATIONALS_ID = 120
NATIONALS_NAME = "Washington Nationals"
MISSING_GAME_PK = 776640


def league_client_missing_one_mariners_game() -> FakeLeagueMlb:
    """A three-club league whose middle club is missing a completed game.

    Discovery sorts by name, so the clubs are visited Cubs, Mariners,
    Nationals. Putting the failure in the middle is what proves the run
    continues rather than stopping at the first bad club.
    """
    mariners_log = drop_game_log_splits(
        retarget(
            load_payload("cubs_2025_hitting_game_log"), MARINERS_ID, MARINERS_NAME
        ),
        MISSING_GAME_PK,
    )
    return FakeLeagueMlb(
        teams=[
            make_team(CUBS_ID, CUBS_NAME),
            make_team(MARINERS_ID, MARINERS_NAME),
            make_team(NATIONALS_ID, NATIONALS_NAME),
        ],
        sources={
            CUBS_ID: build_source(CUBS_ID, CUBS_NAME),
            MARINERS_ID: build_source(
                MARINERS_ID, MARINERS_NAME, team_stats=build_team_stats(mariners_log)
            ),
            NATIONALS_ID: build_source(NATIONALS_ID, NATIONALS_NAME),
        },
    )


def test_a_short_team_season_does_not_abort_the_league(
    migrated_session: Session,
) -> None:
    client = league_client_missing_one_mariners_game()
    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=client
    )

    assert result.status is LeagueSeasonIngestionStatus.INCOMPLETE
    assert (result.teams_discovered, result.teams_succeeded, result.teams_failed) == (
        3,
        2,
        1,
    )


def test_every_club_after_the_failure_is_still_attempted(
    migrated_session: Session,
) -> None:
    client = league_client_missing_one_mariners_game()
    ingest_league_season(session=migrated_session, season=SEASON, client=client)
    # Two stat-group requests per club, hitting then pitching. The Mariners
    # appear once because their hitting log is short: the failure is raised
    # before their pitching log is ever asked for, which is the point of
    # fetching everything before writing anything.
    assert client.team_stats_calls == [
        CUBS_ID,
        CUBS_ID,
        MARINERS_ID,
        NATIONALS_ID,
        NATIONALS_ID,
    ]


def test_the_short_team_season_writes_no_partial_rows(
    migrated_session: Session,
) -> None:
    """Normalization fails before the team's transaction ever begins."""
    client = league_client_missing_one_mariners_game()
    ingest_league_season(session=migrated_session, season=SEASON, client=client)
    assert list_team_season(migrated_session, team_id=MARINERS_ID, season=SEASON) == []


def test_clubs_around_the_failure_remain_committed(
    migrated_session: Session,
) -> None:
    client = league_client_missing_one_mariners_game()
    ingest_league_season(session=migrated_session, season=SEASON, client=client)
    for team_id in (CUBS_ID, NATIONALS_ID):
        stored = list_team_season(migrated_session, team_id=team_id, season=SEASON)
        assert len(stored) == CUBS_GAME_COUNT
    assert stored_row_count(migrated_session) == CUBS_GAME_COUNT * 2


def test_the_short_team_season_is_named_in_the_team_results(
    migrated_session: Session,
) -> None:
    client = league_client_missing_one_mariners_game()
    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=client
    )
    failed = [
        team
        for team in result.team_results
        if team.status is LeagueTeamIngestionStatus.FAILED
    ]
    assert [(team.team_id, team.team_name) for team in failed] == [
        (MARINERS_ID, MARINERS_NAME)
    ]
    assert failed[0].error is not None
    assert "TeamGameDataError" in failed[0].error
    assert str(MISSING_GAME_PK) in failed[0].error


def test_a_short_team_season_records_incomplete_coverage(
    migrated_session: Session,
) -> None:
    client = league_client_missing_one_mariners_game()
    ingest_league_season(session=migrated_session, season=SEASON, client=client)
    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.INCOMPLETE
    assert (
        state.expected_team_count,
        state.successful_team_count,
        state.failed_team_count,
    ) == (3, 2, 1)


# --------------------------------------------------------------------------
# Duplicate discovery stops the run before anything is attempted or recorded
# --------------------------------------------------------------------------


def duplicate_discovery_client() -> FakeLeagueMlb:
    return FakeLeagueMlb(
        teams=[
            make_team(CUBS_ID, CUBS_NAME),
            make_team(MARINERS_ID, MARINERS_NAME),
            make_team(CUBS_ID, CUBS_NAME),
        ],
        sources={
            CUBS_ID: build_source(CUBS_ID, CUBS_NAME),
            MARINERS_ID: build_source(MARINERS_ID, MARINERS_NAME),
        },
    )


def test_duplicate_discovery_fails_the_league_run(migrated_session: Session) -> None:
    with pytest.raises(MlbTeamDiscoveryError):
        ingest_league_season(
            session=migrated_session, season=SEASON, client=duplicate_discovery_client()
        )


def test_duplicate_discovery_starts_no_team_ingestion(
    migrated_session: Session,
) -> None:
    client = duplicate_discovery_client()
    with pytest.raises(MlbTeamDiscoveryError):
        ingest_league_season(session=migrated_session, season=SEASON, client=client)
    assert client.team_stats_calls == []
    assert stored_row_count(migrated_session) == 0


def test_duplicate_discovery_writes_no_coverage_row(
    migrated_session: Session,
) -> None:
    """Coverage is recorded only once the set of teams to cover is trustworthy."""
    with pytest.raises(MlbTeamDiscoveryError):
        ingest_league_season(
            session=migrated_session, season=SEASON, client=duplicate_discovery_client()
        )
    assert get_league_season_ingestion(migrated_session, season=SEASON) is None


# --------------------------------------------------------------------------
# Coverage is never recorded COMPLETE before the result model accepts the run
# --------------------------------------------------------------------------


def test_final_result_validation_failure_leaves_coverage_running(
    migrated_session: Session,
) -> None:
    """The database must not claim COMPLETE for a result the domain rejects.

    The result model is replaced at the service's own boundary so that building
    it fails the way a genuine invariant breach would, without weakening the
    real validators that exist to catch exactly that.
    """
    with (
        patch(
            "app.services.league_season_ingestion.LeagueSeasonIngestionResult",
            side_effect=ValueError("league result rejected"),
        ),
        pytest.raises(ValueError, match="league result rejected"),
    ):
        ingest_league_season(
            session=migrated_session, season=SEASON, client=make_league_client()
        )

    state = get_league_season_ingestion(migrated_session, season=SEASON)
    assert state is not None
    assert state.status is not LeagueSeasonIngestionStatus.COMPLETE
    assert state.status is LeagueSeasonIngestionStatus.RUNNING
    assert state.completed_at is None


def test_a_rejected_final_result_still_leaves_ingested_teams_committed(
    migrated_session: Session,
) -> None:
    """The teams really were ingested; only the coverage claim is withheld."""
    with (
        patch(
            "app.services.league_season_ingestion.LeagueSeasonIngestionResult",
            side_effect=ValueError("league result rejected"),
        ),
        pytest.raises(ValueError),
    ):
        ingest_league_season(
            session=migrated_session, season=SEASON, client=make_league_client()
        )
    assert stored_row_count(migrated_session) == CUBS_GAME_COUNT * 2
