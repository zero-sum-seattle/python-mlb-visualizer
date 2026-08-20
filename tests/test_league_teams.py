"""Tests for season-aware MLB team discovery.

Nothing here touches the network. ``mlbstatsapi.Mlb`` is replaced at the service
boundary and the clubs it returns are built with the library's own ``Team``
model, so a change to that model's shape breaks these tests rather than passing
silently.
"""

from typing import Any

import pytest
from mlbstatsapi.exceptions import MlbHttpError, MlbTransportError
from mlbstatsapi.models.teams import Team

from app.schemas.teams import MlbTeam
from app.services.league_teams import (
    MlbTeamDiscoveryError,
    NoMlbTeamsDiscoveredError,
    discover_mlb_teams,
)

MLB_SPORT = {"id": 1, "link": "/api/v1/sports/1", "name": "Major League Baseball"}
AAA_SPORT = {"id": 11, "link": "/api/v1/sports/11", "name": "Triple-A"}
SEASON = 2025


def make_team(
    team_id: int,
    name: str,
    *,
    sport: dict[str, Any] | None = None,
    season: int | None = SEASON,
    all_star_status: str | None = "N",
) -> Team:
    """Build one upstream club record the way ``Mlb.get_teams`` returns it."""
    return Team(
        id=team_id,
        link=f"/api/v1/teams/{team_id}",
        name=name,
        sport=MLB_SPORT if sport is None else sport,
        season=season,
        all_star_status=all_star_status,
    )


class FakeTeamDirectory:
    """Stands in for ``mlbstatsapi.Mlb`` at the discovery boundary."""

    def __init__(self, teams: list[Team] | Exception) -> None:
        self._teams = teams
        self.calls: list[dict[str, Any]] = []

    def get_teams(self, sport_id: int = 1, **params: Any) -> list[Team]:
        self.calls.append({"sport_id": sport_id, **params})
        if isinstance(self._teams, Exception):
            raise self._teams
        return self._teams


CUBS = make_team(112, "Chicago Cubs")
MARINERS = make_team(136, "Seattle Mariners")


def test_valid_season_returns_the_discovered_clubs() -> None:
    client = FakeTeamDirectory([CUBS, MARINERS])
    assert discover_mlb_teams(SEASON, client=client) == [
        MlbTeam(team_id=112, team_name="Chicago Cubs", season=SEASON),
        MlbTeam(team_id=136, team_name="Seattle Mariners", season=SEASON),
    ]


def test_the_requested_season_is_sent_upstream() -> None:
    """Season awareness depends entirely on this parameter reaching MLB."""
    client = FakeTeamDirectory([make_team(112, "Chicago Cubs", season=1969)])
    discover_mlb_teams(1969, client=client)
    assert client.calls == [{"sport_id": 1, "season": 1969}]


def test_clubs_are_returned_in_a_stable_order() -> None:
    client = FakeTeamDirectory([MARINERS, CUBS])
    names = [team.team_name for team in discover_mlb_teams(SEASON, client=client)]
    assert names == ["Chicago Cubs", "Seattle Mariners"]


def test_non_major_league_clubs_are_excluded() -> None:
    """A broader upstream response must not pull affiliates into an MLB import."""
    affiliate = make_team(403, "Tacoma Rainiers", sport=AAA_SPORT)
    client = FakeTeamDirectory([CUBS, affiliate])
    assert [team.team_id for team in discover_mlb_teams(SEASON, client=client)] == [112]


def test_clubs_without_a_sport_are_excluded() -> None:
    unknown = Team(id=999, link="/api/v1/teams/999", name="Mystery Club", season=SEASON)
    client = FakeTeamDirectory([CUBS, unknown])
    assert [team.team_id for team in discover_mlb_teams(SEASON, client=client)] == [112]


def test_all_star_squads_are_excluded() -> None:
    """They carry the Major League sport id but play no regular season."""
    all_stars = make_team(159, "American League All-Stars", all_star_status="Y")
    client = FakeTeamDirectory([CUBS, all_stars])
    assert [team.team_id for team in discover_mlb_teams(SEASON, client=client)] == [112]


def test_a_historical_season_keeps_its_contemporary_club_set() -> None:
    """1969 had 24 clubs under names that no longer all exist.

    The point of this test is that discovery reports whatever MLB returns for
    the requested season rather than reconciling it against today's 30 clubs.
    """
    expansion_era = [
        make_team(109, "Seattle Pilots", season=1969),
        make_team(120, "Washington Senators", season=1969),
        make_team(112, "Chicago Cubs", season=1969),
    ]
    client = FakeTeamDirectory(expansion_era)
    discovered = discover_mlb_teams(1969, client=client)
    assert [(team.team_id, team.team_name) for team in discovered] == [
        (112, "Chicago Cubs"),
        (109, "Seattle Pilots"),
        (120, "Washington Senators"),
    ]
    assert {team.season for team in discovered} == {1969}


def test_upstream_failure_is_wrapped_with_context() -> None:
    client = FakeTeamDirectory(MlbTransportError("Request failed"))
    with pytest.raises(MlbTeamDiscoveryError, match="2025"):
        discover_mlb_teams(SEASON, client=client)


def test_upstream_failure_preserves_exception_chaining() -> None:
    cause = MlbHttpError(500, "Internal Server Error")
    client = FakeTeamDirectory(cause)
    with pytest.raises(MlbTeamDiscoveryError) as exc_info:
        discover_mlb_teams(SEASON, client=client)
    assert exc_info.value.__cause__ is cause


def test_empty_discovery_is_a_failure_not_an_empty_league() -> None:
    """The library returns [] for a 4xx, so [] cannot mean "no teams played"."""
    client = FakeTeamDirectory([])
    with pytest.raises(NoMlbTeamsDiscoveredError, match="2025"):
        discover_mlb_teams(SEASON, client=client)


def test_a_response_of_only_ineligible_clubs_is_a_failure() -> None:
    client = FakeTeamDirectory([make_team(403, "Tacoma Rainiers", sport=AAA_SPORT)])
    with pytest.raises(NoMlbTeamsDiscoveredError):
        discover_mlb_teams(SEASON, client=client)


def test_a_club_reporting_another_season_is_refused() -> None:
    """A silently ignored season parameter would ingest the wrong club set."""
    client = FakeTeamDirectory([make_team(112, "Chicago Cubs", season=2024)])
    with pytest.raises(MlbTeamDiscoveryError, match="reports season 2024"):
        discover_mlb_teams(SEASON, client=client)


def test_a_club_without_a_season_is_accepted() -> None:
    """The field is optional upstream; its absence is not a contradiction."""
    client = FakeTeamDirectory([make_team(112, "Chicago Cubs", season=None)])
    assert discover_mlb_teams(SEASON, client=client) == [
        MlbTeam(team_id=112, team_name="Chicago Cubs", season=SEASON)
    ]


def test_a_club_without_a_name_is_refused() -> None:
    nameless = Team(id=112, link="/api/v1/teams/112", sport=MLB_SPORT, season=SEASON)
    client = FakeTeamDirectory([nameless])
    with pytest.raises(MlbTeamDiscoveryError, match="without a name"):
        discover_mlb_teams(SEASON, client=client)


def test_discovery_opens_and_closes_its_own_client_when_none_is_given() -> None:
    """Callers that already hold a client pass it in; a lone call owns one."""
    from unittest.mock import patch

    directory = FakeTeamDirectory([CUBS])
    closed: list[bool] = []

    class OwnedClient:
        def __enter__(self) -> FakeTeamDirectory:
            return directory

        def __exit__(self, *args: object) -> None:
            closed.append(True)

    with patch("app.services.league_teams.Mlb", return_value=OwnedClient()):
        assert discover_mlb_teams(SEASON) == [
            MlbTeam(team_id=112, team_name="Chicago Cubs", season=SEASON)
        ]
    assert closed == [True]
