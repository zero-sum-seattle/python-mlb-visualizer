"""Discover the Major League clubs for a season over the async MLB client.

This module is **transport only**. Every decision about what a discovery
response means — which clubs are eligible, what each record must carry, that a
repeated team id is refused rather than deduplicated, that an empty result is a
failure rather than an answer, and the stable order clubs are returned in —
belongs to ``app.services.league_teams`` and is called from here, not restated.

``discover_mlb_teams`` in that module remains the reference implementation. The
only thing that differs here is which client makes the one request and that the
call is awaited.

Nothing in this module is concurrent. Discovery is a single MLB request; there
is nothing to overlap. It is async so that a caller already running in an event
loop can reuse one ``AsyncMlb`` client for discovery and for the team fetches
that follow.
"""

from typing import Protocol

from mlbstatsapi import AsyncMlb
from mlbstatsapi.models.teams import Team

from app.schemas.teams import MlbTeam
from app.services.league_teams import (
    normalize_discovered_teams,
    translating_discovery_failure,
)
from app.services.team_game_logs import MLB_SPORT_ID


class AsyncMlbTeamDirectoryClient(Protocol):
    """The subset of ``mlbstatsapi.AsyncMlb`` team discovery depends on."""

    async def get_teams(
        self,
        sport_id: int = ...,
        **params: object,
    ) -> list[Team]: ...


async def discover_mlb_teams_async(
    season: int,
    *,
    client: AsyncMlbTeamDirectoryClient | None = None,
) -> list[MlbTeam]:
    """Return every Major League club MLB reports for ``season``.

    The async counterpart of ``league_teams.discover_mlb_teams``. Same clubs,
    same eligibility rules, same order, same errors.

    Parameters
    ----------
    season:
        Four digit season year.
    client:
        An existing ``mlbstatsapi.AsyncMlb`` client. When omitted a client is
        created and closed for this call.

    Raises
    ------
    NoMlbTeamsDiscoveredError
        MLB returned no Major League clubs for the season.
    MlbTeamDiscoveryError
        The upstream request failed, or a returned club could not be trusted.
    """
    if client is not None:
        return await _discover(client, season)
    async with AsyncMlb() as owned_client:
        return await _discover(owned_client, season)


async def _discover(
    client: AsyncMlbTeamDirectoryClient,
    season: int,
) -> list[MlbTeam]:
    with translating_discovery_failure(season):
        teams = await client.get_teams(sport_id=MLB_SPORT_ID, season=season)
    return normalize_discovered_teams(teams, season)
