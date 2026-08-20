"""Discover the Major League clubs that played a requested season.

Milestone 4 needs to know *which* teams a league-wide ingestion is supposed to
cover before it can claim the season was covered. Hardcoding today's 30 club ids
would answer that question wrongly for every earlier season, so the list is
asked of MLB for the season under ingestion.

Upstream call
-------------
``mlbstatsapi.Mlb.get_teams(sport_id=1, season=<year>)`` — the library's wrapper
around ``GET https://statsapi.mlb.com/api/v1/teams?sportId=1&season=<year>``. It
returns ``list[mlbstatsapi.models.teams.Team]``; a 4xx response yields an empty
list rather than raising, so an empty result is treated here as a discovery
failure rather than as "this season had no teams".

``sportId=1`` is Major League Baseball. ``season`` is the four digit year and is
what makes the answer season aware: MLB resolves the club set, and each club's
name, as of that season. See ``docs/league-season-ingestion.md`` for the
season-aware behavior this relies on and its limitations.
"""

from typing import Protocol

from mlbstatsapi import Mlb
from mlbstatsapi.exceptions import TheMlbStatsApiException
from mlbstatsapi.models.teams import Team

from app.schemas.teams import MlbTeam
from app.services.team_game_logs import MLB_SPORT_ID

ALL_STAR_TEAM_STATUS = "Y"


class MlbTeamDiscoveryError(Exception):
    """The MLB teams for a season could not be retrieved or understood."""


class NoMlbTeamsDiscoveredError(MlbTeamDiscoveryError):
    """MLB returned no eligible Major League clubs for the season."""


class MlbTeamDirectoryClient(Protocol):
    """The subset of ``mlbstatsapi.Mlb`` team discovery depends on."""

    def get_teams(self, sport_id: int = ..., **params: object) -> list[Team]: ...


def discover_mlb_teams(
    season: int,
    *,
    client: MlbTeamDirectoryClient | None = None,
) -> list[MlbTeam]:
    """Return every Major League club MLB reports for ``season``.

    Clubs are returned sorted by name then id so a league-wide ingestion visits
    them in a stable, reproducible order.

    Parameters
    ----------
    season:
        Four digit season year.
    client:
        An existing ``mlbstatsapi.Mlb`` client. When omitted a client is created
        and closed for this call.

    Raises
    ------
    NoMlbTeamsDiscoveredError
        MLB returned no Major League clubs for the season.
    MlbTeamDiscoveryError
        The upstream request failed, or a returned club could not be trusted.
    """
    if client is not None:
        return _discover(client, season)
    with Mlb() as owned_client:
        return _discover(owned_client, season)


def _discover(client: MlbTeamDirectoryClient, season: int) -> list[MlbTeam]:
    try:
        teams = client.get_teams(sport_id=MLB_SPORT_ID, season=season)
    except TheMlbStatsApiException as exc:
        raise MlbTeamDiscoveryError(
            f"Unable to retrieve the MLB teams for {season}"
        ) from exc

    discovered = [
        _normalize_team(team, season) for team in teams if _is_major_league_club(team)
    ]
    if not discovered:
        raise NoMlbTeamsDiscoveredError(
            f"MLB returned no Major League teams for {season}"
        )
    _reject_duplicate_team_ids(discovered, season)
    return sorted(discovered, key=lambda team: (team.team_name, team.team_id))


def _reject_duplicate_team_ids(teams: list[MlbTeam], season: int) -> None:
    """Refuse a discovery response that names the same club twice.

    A repeated team id is not deduplicated. Downstream, ``teams_discovered``
    is what league coverage is measured against, so a silently collapsed
    duplicate would mean the number of teams the run claims to have covered no
    longer matches the number MLB reported. A club appearing twice also means
    upstream identity is unreliable for this season, which is a data-integrity
    problem to surface rather than tidy away.

    Two different names under one id is the same failure, not a worse one: the
    id is the identity every downstream record is keyed by.
    """
    seen: dict[int, str] = {}
    for team in teams:
        known = seen.get(team.team_id)
        if known is None:
            seen[team.team_id] = team.team_name
            continue
        names = (
            f"twice as {known!r}"
            if known == team.team_name
            else f"as both {known!r} and {team.team_name!r}"
        )
        raise MlbTeamDiscoveryError(
            f"MLB returned team {team.team_id} more than once for {season} ({names})"
        )


def _is_major_league_club(team: Team) -> bool:
    """Keep only clubs that can have a Major League regular-season game log.

    ``sportId=1`` already scopes the request, but the sport is re-checked on
    each record so a broader upstream response, or a caller passing extra
    parameters, cannot smuggle a minor league or other-sport club into a
    league-wide MLB ingestion. All-Star squads are excluded for the same
    reason: they carry the Major League sport id but play no regular season.
    """
    if team.sport is None or team.sport.id != MLB_SPORT_ID:
        return False
    return team.all_star_status != ALL_STAR_TEAM_STATUS


def _normalize_team(team: Team, season: int) -> MlbTeam:
    """Convert one upstream club into the domain model, refusing bad records.

    ``Team.name`` and ``Team.season`` are both optional upstream. A club with no
    name cannot be reported to an operator, and a club stamped with a different
    season means the ``season`` parameter was not honored, which would silently
    ingest the wrong club set. Both are data-integrity failures rather than
    values to guess at.
    """
    if not team.name:
        raise MlbTeamDiscoveryError(
            f"MLB team {team.id} was returned for {season} without a name"
        )
    if team.season is not None and team.season != season:
        raise MlbTeamDiscoveryError(
            f"MLB team {team.id} ({team.name}) reports season {team.season} but "
            f"{season} was requested"
        )
    return MlbTeam(team_id=team.id, team_name=team.name, season=season)
