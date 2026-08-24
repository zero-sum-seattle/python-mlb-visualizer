"""Tests for the async MLB transports.

Two modules are covered: ``async_team_game_logs``, which fetches one club's
team-season, and ``async_league_teams``, which fetches a season's club list.
Neither decides anything about baseball data, so what these tests establish is
that they ask MLB for the same thing the synchronous path asks for, in the same
order, and hand the answers to the same rules.

The client double wraps the fake the synchronous tests already use, so both
paths are driven by identical captured fixtures. Nothing here touches the
network.
"""

import asyncio
from typing import Any

import pytest
from mlbstatsapi.exceptions import MlbTransportError
from mlbstatsapi.models.schedules import Schedule
from mlbstatsapi.models.teams import Team

from app.services import async_league_teams, async_team_game_logs
from app.services.async_league_teams import discover_mlb_teams_async
from app.services.async_team_game_logs import get_team_game_lines_async
from app.services.league_teams import (
    MlbTeamDiscoveryError,
    NoMlbTeamsDiscoveredError,
    discover_mlb_teams,
)
from app.services.team_game_logs import (
    TeamGameDataError,
    TeamGameLogError,
    TeamNotFoundError,
    get_team_game_lines,
)
from tests.test_league_teams import make_team
from tests.test_team_game_logs import (
    CUBS_ID,
    SEASON,
    FakeMlb,
    build_schedule,
    client_missing,
    load_payload,
    make_client,
)


class AsyncFakeMlb:
    """Answer the synchronous ``FakeMlb`` from coroutines, in call order."""

    def __init__(self, sync: FakeMlb, *, teams: list[Team] | None = None) -> None:
        self.sync = sync
        self._teams = teams or []
        self.call_order: list[str] = []
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self) -> "AsyncFakeMlb":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def get_team(self, team_id: int, **params: Any) -> Team | None:
        self.call_order.append("get_team")
        return self.sync.get_team(team_id, **params)

    async def get_team_stats(
        self,
        team_id: int,
        stats: list[str],
        groups: list[str],
        **params: Any,
    ) -> dict[str, Any]:
        self.call_order.append(f"get_team_stats:{','.join(groups)}")
        return self.sync.get_team_stats(team_id, stats, groups, **params)

    async def get_schedule(self, **params: Any) -> Schedule | None:
        self.call_order.append("get_schedule")
        return self.sync.get_schedule(**params)

    async def get_teams(self, sport_id: int = 1, **params: Any) -> list[Team]:
        self.call_order.append("get_teams")
        return self._teams


def fetch_async(client: AsyncFakeMlb) -> tuple[list[Any], list[Any]]:
    return asyncio.run(get_team_game_lines_async(CUBS_ID, SEASON, client=client))


# --------------------------------------------------------------------------
# The same records, from the same fixtures
# --------------------------------------------------------------------------


def test_the_async_path_produces_the_same_lines_as_the_synchronous_one() -> None:
    """Identical fixtures in, identical domain records out."""
    sync_batting, sync_pitching = get_team_game_lines(
        CUBS_ID, SEASON, client=make_client()
    )
    async_batting, async_pitching = fetch_async(AsyncFakeMlb(make_client()))

    assert async_batting == sync_batting
    assert async_pitching == sync_pitching
    assert async_batting != []


def test_the_async_path_asks_mlb_for_the_same_thing() -> None:
    """The request parameters come from the shared builders, not from here."""
    sync_client = make_client()
    get_team_game_lines(CUBS_ID, SEASON, client=sync_client)

    async_client = AsyncFakeMlb(make_client())
    fetch_async(async_client)

    assert async_client.sync.calls["get_team"] == sync_client.calls["get_team"]
    assert async_client.sync.calls["get_schedule"] == sync_client.calls["get_schedule"]
    assert (
        async_client.sync.calls["get_team_stats"]
        == (sync_client.calls["get_team_stats"])
    )
    assert async_client.sync.stat_group_calls == sync_client.stat_group_calls


def test_a_team_season_costs_four_requests() -> None:
    client = AsyncFakeMlb(make_client())
    fetch_async(client)
    assert len(client.call_order) == 4


def test_the_four_requests_happen_in_the_documented_order() -> None:
    """Team, schedule, hitting, pitching — one at a time, never overlapped."""
    client = AsyncFakeMlb(make_client())
    fetch_async(client)
    assert client.call_order == [
        "get_team",
        "get_schedule",
        "get_team_stats:hitting",
        "get_team_stats:pitching",
    ]


# --------------------------------------------------------------------------
# The same failures, raised at the same point
# --------------------------------------------------------------------------


def test_an_unknown_club_is_refused_before_anything_else_is_requested() -> None:
    client = AsyncFakeMlb(FakeMlb(team=None))
    with pytest.raises(TeamNotFoundError):
        fetch_async(client)
    assert client.call_order == ["get_team"]


def test_an_upstream_team_lookup_failure_is_reported_identically() -> None:
    """The wording and the error type come from the shared translation."""
    with pytest.raises(TeamGameLogError) as sync_error:
        get_team_game_lines(
            CUBS_ID, SEASON, client=FakeMlb(team=MlbTransportError("Request failed"))
        )
    with pytest.raises(TeamGameLogError) as async_error:
        fetch_async(AsyncFakeMlb(FakeMlb(team=MlbTransportError("Request failed"))))

    assert str(async_error.value) == str(sync_error.value)
    assert type(async_error.value) is type(sync_error.value)


def test_a_missing_schedule_is_refused_before_either_game_log() -> None:
    client = AsyncFakeMlb(FakeMlb(schedule=None))
    with pytest.raises(TeamGameDataError):
        fetch_async(client)
    assert client.call_order == ["get_team", "get_schedule"]


def test_a_missing_hitting_game_log_is_refused_before_the_pitching_request() -> None:
    client = AsyncFakeMlb(
        FakeMlb(
            team_stats={},
            schedule=build_schedule(load_payload("cubs_2025_schedule")),
        )
    )
    with pytest.raises(TeamGameDataError):
        fetch_async(client)
    assert "get_team_stats:pitching" not in client.call_order


def test_a_team_season_missing_a_completed_game_is_refused() -> None:
    """The reverse completeness check is the shared one, not a second copy."""
    sync_client = client_missing(776640)
    async_client = AsyncFakeMlb(client_missing(776640))

    with pytest.raises(TeamGameDataError) as sync_error:
        get_team_game_lines(CUBS_ID, SEASON, client=sync_client)
    with pytest.raises(TeamGameDataError) as async_error:
        fetch_async(async_client)

    assert str(async_error.value) == str(sync_error.value)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def discover_async(teams: list[Team]) -> list[Any]:
    client = AsyncFakeMlb(FakeMlb(), teams=teams)
    return asyncio.run(discover_mlb_teams_async(SEASON, client=client))


def test_async_discovery_returns_the_same_clubs_in_the_same_order() -> None:
    teams = [make_team(136, "Seattle Mariners"), make_team(CUBS_ID, "Chicago Cubs")]

    class SyncDirectory:
        def get_teams(self, sport_id: int = 1, **params: Any) -> list[Team]:
            return teams

    assert discover_async(teams) == discover_mlb_teams(SEASON, client=SyncDirectory())


def test_async_discovery_refuses_an_empty_response() -> None:
    with pytest.raises(NoMlbTeamsDiscoveredError):
        discover_async([])


def test_async_discovery_refuses_a_repeated_team_id() -> None:
    with pytest.raises(MlbTeamDiscoveryError):
        discover_async([make_team(CUBS_ID, "Chicago Cubs"), make_team(CUBS_ID, "Cubs")])


# --------------------------------------------------------------------------
# Client ownership
# --------------------------------------------------------------------------


def test_a_client_created_for_one_team_season_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncFakeMlb(make_client())
    monkeypatch.setattr(async_team_game_logs, "AsyncMlb", lambda: client)

    batting, _ = asyncio.run(get_team_game_lines_async(CUBS_ID, SEASON))

    assert batting != []
    assert client.closed is True


def test_a_supplied_client_is_not_closed() -> None:
    client = AsyncFakeMlb(make_client())
    fetch_async(client)
    assert client.closed is False


def test_a_client_created_for_discovery_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncFakeMlb(FakeMlb(), teams=[make_team(CUBS_ID, "Chicago Cubs")])
    monkeypatch.setattr(async_league_teams, "AsyncMlb", lambda: client)

    assert asyncio.run(discover_mlb_teams_async(SEASON)) != []
    assert client.closed is True
