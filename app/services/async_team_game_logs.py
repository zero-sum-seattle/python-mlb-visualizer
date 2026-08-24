"""Retrieve one team-season's game logs over the async MLB client.

This module is **transport only**. It makes the same four MLB requests
``team_game_logs`` makes, in the same order, and hands every answer to the same
functions the synchronous path uses. Which parameters are sent, which responses
are usable, how a game log joins the schedule, which games count as completed,
and how a split becomes a domain record all live in ``team_game_logs`` and are
called from here. No rule about baseball data is decided in this file.

``team_game_logs.get_team_game_lines`` remains the reference implementation.

What is and is not concurrent here
----------------------------------
Nothing in this module is concurrent. The four requests for one club are
awaited **one at a time**, in the order the synchronous path makes them:

    team lookup ─► schedule ─► hitting game log ─► pitching game log

That is deliberate. Overlapping the four would cap out at a 4x saving on one
club, would mean a club's requests are in flight after the answer that would
have refused the club has already arrived, and would change which error a
broken club reports. Concurrency belongs at the fan-out over clubs instead, and
lives in ``concurrent_league_season_ingestion``.

Awaiting in this order keeps failures identical to the synchronous path: a club
that is not a Major League team is refused before its schedule is requested, a
missing schedule before either game log, and a batting line that cannot be
normalized before the pitching log is requested.
"""

from typing import Protocol

from mlbstatsapi import AsyncMlb
from mlbstatsapi.models.schedules import Schedule
from mlbstatsapi.models.stats import HittingGameLog, PitchingGameLog
from mlbstatsapi.models.teams import Team

from app.schemas.games import TeamGameBattingLine, TeamGamePitchingLine
from app.services.team_game_logs import (
    HITTING_STAT_GROUP,
    PITCHING_STAT_GROUP,
    index_schedule_games,
    normalize_batting_log,
    normalize_pitching_log,
    require_hitting_game_log,
    require_mlb_team,
    require_pitching_game_log,
    require_team_schedule,
    team_game_log_request,
    team_schedule_request,
    translating_game_data_failure,
    translating_team_lookup_failure,
)


class AsyncMlbGameDataClient(Protocol):
    """The subset of ``mlbstatsapi.AsyncMlb`` this service depends on."""

    async def get_team(
        self,
        team_id: int,
        season: int = ...,
        **params: object,
    ) -> Team | None: ...

    async def get_team_stats(
        self,
        team_id: int,
        stats: list[str],
        groups: list[str],
        **params: object,
    ) -> dict: ...

    async def get_schedule(
        self,
        date: str | None = ...,
        start_date: str | None = ...,
        end_date: str | None = ...,
        sport_id: int = ...,
        team_id: int | None = ...,
        **params: object,
    ) -> Schedule | None: ...


async def get_team_game_lines_async(
    team_id: int,
    season: int,
    *,
    client: AsyncMlbGameDataClient | None = None,
) -> tuple[list[TeamGameBattingLine], list[TeamGamePitchingLine]]:
    """Return both the batting and pitching lines for one team-season.

    The async counterpart of ``team_game_logs.get_team_game_lines``. Same four
    requests, same order, same normalization, same errors, same records.

    Parameters
    ----------
    team_id:
        MLB team id, for example 136 for the Seattle Mariners.
    season:
        Four digit season year.
    client:
        An existing ``mlbstatsapi.AsyncMlb`` client. When omitted a client is
        created and closed for this call.

    Raises
    ------
    TeamNotFoundError
        The team id is not an MLB team.
    TeamGameDataError
        The upstream response was empty or could not be normalized.
    TeamGameLogError
        The upstream request failed.
    """
    if client is not None:
        return await _collect_both_lines(client, team_id, season)
    async with AsyncMlb() as owned_client:
        return await _collect_both_lines(owned_client, team_id, season)


async def _collect_both_lines(
    client: AsyncMlbGameDataClient,
    team_id: int,
    season: int,
) -> tuple[list[TeamGameBattingLine], list[TeamGamePitchingLine]]:
    team = await _fetch_mlb_team(client, team_id, season)
    scheduled_games = index_schedule_games(
        await _fetch_schedule(client, team_id, season)
    )
    batting = normalize_batting_log(
        await _fetch_hitting_game_log(client, team_id, season),
        team=team,
        season=season,
        scheduled_games=scheduled_games,
    )
    pitching = normalize_pitching_log(
        await _fetch_pitching_game_log(client, team_id, season),
        team=team,
        season=season,
        scheduled_games=scheduled_games,
    )
    return batting, pitching


async def _fetch_mlb_team(
    client: AsyncMlbGameDataClient,
    team_id: int,
    season: int,
) -> Team:
    with translating_team_lookup_failure(team_id, season):
        team = await client.get_team(team_id, season=season)
    return require_mlb_team(team, team_id=team_id, season=season)


async def _fetch_schedule(
    client: AsyncMlbGameDataClient,
    team_id: int,
    season: int,
) -> Schedule:
    with translating_game_data_failure():
        schedule = await client.get_schedule(
            **team_schedule_request(team_id=team_id, season=season),
        )
    return require_team_schedule(schedule, team_id=team_id, season=season)


async def _fetch_hitting_game_log(
    client: AsyncMlbGameDataClient,
    team_id: int,
    season: int,
) -> list[HittingGameLog]:
    with translating_game_data_failure():
        stat_groups = await client.get_team_stats(
            team_id,
            **team_game_log_request(season=season, group=HITTING_STAT_GROUP),
        )
    return require_hitting_game_log(stat_groups, team_id=team_id, season=season)


async def _fetch_pitching_game_log(
    client: AsyncMlbGameDataClient,
    team_id: int,
    season: int,
) -> list[PitchingGameLog]:
    with translating_game_data_failure():
        stat_groups = await client.get_team_stats(
            team_id,
            **team_game_log_request(season=season, group=PITCHING_STAT_GROUP),
        )
    return require_pitching_game_log(stat_groups, team_id=team_id, season=season)
