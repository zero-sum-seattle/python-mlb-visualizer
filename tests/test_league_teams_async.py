"""Tests for the async MLB team discovery path.

Mirrors ``test_league_teams.py`` rather than duplicating it: the async and
sync entry points share ``_finish_discovery`` (filtering, normalization, and
the duplicate-id check), so these tests only need to prove the async
transport reaches that shared code the same way the sync transport does.
Nothing here touches the network.
"""

import asyncio
from typing import Any

import pytest
from mlbstatsapi.exceptions import MlbTransportError

from app.schemas.teams import MlbTeam
from app.services.league_teams import (
    MlbTeamDiscoveryError,
    NoMlbTeamsDiscoveredError,
    discover_mlb_teams,
    discover_mlb_teams_async,
)
from tests.test_league_teams import (
    AAA_SPORT,
    CUBS,
    MARINERS,
    SEASON,
    FakeTeamDirectory,
    make_team,
)

CUBS_AGAIN = make_team(112, "Chicago Cubs")


class AsyncFakeTeamDirectory:
    """Async counterpart of ``FakeTeamDirectory``."""

    def __init__(self, teams: list) -> None:
        self._teams = teams
        self.calls: list[dict[str, Any]] = []

    async def get_teams(self, sport_id: int = 1, **params: Any) -> list:
        self.calls.append({"sport_id": sport_id, **params})
        if isinstance(self._teams, Exception):
            raise self._teams
        return self._teams


def test_async_discovery_matches_sync() -> None:
    sync_result = discover_mlb_teams(SEASON, client=FakeTeamDirectory([CUBS, MARINERS]))
    async_result = asyncio.run(
        discover_mlb_teams_async(
            SEASON, client=AsyncFakeTeamDirectory([CUBS, MARINERS])
        )
    )
    assert async_result == sync_result
    assert async_result == [
        MlbTeam(team_id=112, team_name="Chicago Cubs", season=SEASON),
        MlbTeam(team_id=136, team_name="Seattle Mariners", season=SEASON),
    ]


def test_async_request_parameters_match_sync() -> None:
    client = AsyncFakeTeamDirectory([CUBS])
    asyncio.run(discover_mlb_teams_async(SEASON, client=client))
    assert client.calls == [{"sport_id": 1, "season": SEASON}]


def test_async_excludes_non_major_league_clubs() -> None:
    affiliate = make_team(403, "Tacoma Rainiers", sport=AAA_SPORT)
    client = AsyncFakeTeamDirectory([CUBS, affiliate])
    result = asyncio.run(discover_mlb_teams_async(SEASON, client=client))
    assert [team.team_id for team in result] == [112]


def test_async_upstream_failure_is_wrapped_the_same_way() -> None:
    client = AsyncFakeTeamDirectory(MlbTransportError("Request failed"))
    with pytest.raises(MlbTeamDiscoveryError, match="2025"):
        asyncio.run(discover_mlb_teams_async(SEASON, client=client))


def test_async_empty_discovery_is_a_failure() -> None:
    client = AsyncFakeTeamDirectory([])
    with pytest.raises(NoMlbTeamsDiscoveredError, match="2025"):
        asyncio.run(discover_mlb_teams_async(SEASON, client=client))


def test_async_duplicate_team_id_is_refused_the_same_way() -> None:
    client = AsyncFakeTeamDirectory([CUBS, MARINERS, CUBS_AGAIN])
    with pytest.raises(MlbTeamDiscoveryError) as excinfo:
        asyncio.run(discover_mlb_teams_async(SEASON, client=client))
    assert "112" in str(excinfo.value)
