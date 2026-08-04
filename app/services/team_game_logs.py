"""Retrieve and normalize a team's game-level hitting results.

Hitting numbers come from the team ``gameLog`` hitting stat type, which returns
one split per played game in a single request. Game context that the stat split
does not carry (status, opponent name, game number, scheduled innings) comes
from a single team schedule request and is joined on ``gamePk``.

The two sources overlap on team, opponent, home/away, date, and runs. That
overlap is validated for every game rather than assumed, so an upstream or
package-model change surfaces as a ``TeamGameDataError`` instead of a silently
wrong record.

See ``docs/team-game-data-spike.md`` for the investigation behind this choice.
"""

from datetime import date
from typing import Protocol

from mlbstatsapi import Mlb
from mlbstatsapi.exceptions import TheMlbStatsApiException
from mlbstatsapi.models.schedules import Schedule, ScheduleGames, ScheduleGameTeam
from mlbstatsapi.models.stats import HittingGameLog
from mlbstatsapi.models.teams import Team
from pydantic import ValidationError

from app.schemas.games import HomeAway, TeamGameBattingLine

MLB_SPORT_ID = 1
REGULAR_SEASON_GAME_TYPE = "R"
HITTING_STAT_GROUP = "hitting"
GAME_LOG_STAT_TYPE = "gameLog"

# ``codedGameState`` values whose team hits and runs are final. "F" is Final and
# "O" is Game Over, which also covers rain-shortened "Completed Early" games.
# Everything else (D postponed, C cancelled, U suspended, I in progress,
# S/P preview, Q/R forfeit) is not a completed batting line.
COMPLETED_CODED_GAME_STATES = frozenset({"F", "O"})


class TeamGameLogError(Exception):
    """Base error for team game log retrieval."""


class TeamNotFoundError(TeamGameLogError):
    """The requested team is not an MLB team."""


class TeamGameDataError(TeamGameLogError):
    """Upstream data was missing or could not be normalized."""


class MlbGameDataClient(Protocol):
    """The subset of ``mlbstatsapi.Mlb`` this service depends on."""

    def get_team(
        self,
        team_id: int,
        season: int = ...,
        **params: object,
    ) -> Team | None: ...

    def get_team_stats(
        self,
        team_id: int,
        stats: list[str],
        groups: list[str],
        **params: object,
    ) -> dict: ...

    def get_schedule(
        self,
        date: str | None = ...,
        start_date: str | None = ...,
        end_date: str | None = ...,
        sport_id: int = ...,
        team_id: int | None = ...,
        **params: object,
    ) -> Schedule | None: ...


def get_team_game_batting_lines(
    team_id: int,
    season: int,
    *,
    client: MlbGameDataClient | None = None,
) -> list[TeamGameBattingLine]:
    """Return the team's batting line for every completed regular-season game.

    Records are sorted by game date, then game number, then game id. Both games
    of a doubleheader are returned as separate records.

    Parameters
    ----------
    team_id:
        MLB team id, for example 136 for the Seattle Mariners.
    season:
        Four digit season year.
    client:
        An existing ``mlbstatsapi.Mlb`` client. When omitted a client is created
        and closed for this call.

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
        return _collect_batting_lines(client, team_id, season)
    with Mlb() as owned_client:
        return _collect_batting_lines(owned_client, team_id, season)


def _collect_batting_lines(
    client: MlbGameDataClient,
    team_id: int,
    season: int,
) -> list[TeamGameBattingLine]:
    team = _fetch_mlb_team(client, team_id, season)
    game_log = _fetch_hitting_game_log(client, team_id, season)
    scheduled_games = _index_schedule_games(_fetch_schedule(client, team_id, season))

    lines: dict[int, TeamGameBattingLine] = {}
    for split in game_log:
        game_pk = split.game.game_pk
        scheduled = scheduled_games.get(game_pk)
        if scheduled is None:
            raise TeamGameDataError(
                f"Game {game_pk} on {split.date} is in the hitting game log for "
                f"team {team_id} but not in the {season} regular-season schedule"
            )
        status = scheduled.status
        if status is None or status.coded_game_state not in COMPLETED_CODED_GAME_STATES:
            continue
        _store_or_validate_duplicate(
            lines,
            _normalize_batting_line(
                split=split,
                scheduled=scheduled,
                status=status.detailed_state,
                team=team,
                season=season,
            ),
        )

    return sorted(
        lines.values(),
        key=lambda line: (line.game_date, line.game_number, line.game_pk),
    )


def _fetch_mlb_team(client: MlbGameDataClient, team_id: int, season: int) -> Team:
    """Look the team up for the requested season to get its name for that season."""
    try:
        team = client.get_team(team_id, season=season)
    except TheMlbStatsApiException as exc:
        raise TeamGameLogError(
            f"Unable to retrieve MLB team {team_id} for {season}"
        ) from exc

    if team is None:
        raise TeamNotFoundError(f"No MLB team found for team id {team_id} in {season}")
    if team.sport is None or team.sport.id != MLB_SPORT_ID:
        raise TeamNotFoundError(
            f"Team {team_id} ({team.name}) is not a Major League Baseball team"
        )
    return team


def _fetch_hitting_game_log(
    client: MlbGameDataClient,
    team_id: int,
    season: int,
) -> list[HittingGameLog]:
    try:
        stat_groups = client.get_team_stats(
            team_id,
            stats=[GAME_LOG_STAT_TYPE],
            groups=[HITTING_STAT_GROUP],
            season=season,
            gameType=REGULAR_SEASON_GAME_TYPE,
        )
    except TheMlbStatsApiException as exc:
        raise TeamGameLogError("Unable to retrieve MLB game data") from exc

    try:
        game_log = stat_groups[HITTING_STAT_GROUP][GAME_LOG_STAT_TYPE]
    except KeyError as exc:
        raise TeamGameDataError(
            f"No regular-season hitting game log returned for team {team_id} "
            f"in {season}"
        ) from exc

    splits = game_log.splits or []
    if not splits:
        raise TeamGameDataError(
            f"The regular-season hitting game log for team {team_id} in {season} "
            f"is empty"
        )
    for split in splits:
        if not isinstance(split, HittingGameLog):
            raise TeamGameDataError(
                f"Unexpected {type(split).__name__} split in the hitting game log "
                f"for team {team_id} in {season}"
            )
    return splits


def _fetch_schedule(client: MlbGameDataClient, team_id: int, season: int) -> Schedule:
    try:
        schedule = client.get_schedule(
            start_date=f"{season}-01-01",
            end_date=f"{season}-12-31",
            sport_id=MLB_SPORT_ID,
            team_id=team_id,
            gameTypes=REGULAR_SEASON_GAME_TYPE,
        )
    except TheMlbStatsApiException as exc:
        raise TeamGameLogError("Unable to retrieve MLB game data") from exc

    if schedule is None:
        raise TeamGameDataError(
            f"No regular-season schedule returned for team {team_id} in {season}"
        )
    return schedule


def _index_schedule_games(schedule: Schedule) -> dict[int, ScheduleGames]:
    """Index a team schedule by ``gamePk``, preferring completed entries.

    A postponed game keeps its ``gamePk`` when it is made up, and a suspended
    game keeps it when it is resumed, so one schedule can list the same game
    twice: once under the original date and once under the date it was played.
    """
    games: dict[int, ScheduleGames] = {}
    for scheduled_date in schedule.dates:
        for game in scheduled_date.games:
            known = games.get(game.game_pk)
            if known is None or not _is_completed(known):
                games[game.game_pk] = game
    return games


def _is_completed(game: ScheduleGames) -> bool:
    return (
        game.status is not None
        and game.status.coded_game_state in COMPLETED_CODED_GAME_STATES
    )


def _store_or_validate_duplicate(
    lines: dict[int, TeamGameBattingLine],
    line: TeamGameBattingLine,
) -> None:
    """Store a normalized line, rejecting a duplicate that disagrees with it.

    The hitting game log is expected to hold one split per game. An identical
    repeat is harmless, but two splits for the same game with different values
    mean the upstream data cannot be trusted for that game.
    """
    known = lines.get(line.game_pk)
    if known is None:
        lines[line.game_pk] = line
        return
    if known == line:
        return

    conflicts = ", ".join(
        f"{field} {getattr(known, field)!r} vs {getattr(line, field)!r}"
        for field in type(line).model_fields
        if getattr(known, field) != getattr(line, field)
    )
    raise TeamGameDataError(
        f"Conflicting duplicate game log records returned for game "
        f"{line.game_pk} on {line.game_date.isoformat()}: {conflicts}"
    )


def _normalize_batting_line(
    *,
    split: HittingGameLog,
    scheduled: ScheduleGames,
    status: str | None,
    team: Team,
    season: int,
) -> TeamGameBattingLine:
    game_pk = split.game.game_pk
    context = f"game {game_pk} on {split.date} for team {team.id}"

    if split.stat is None:
        raise TeamGameDataError(f"No hitting stat line returned for {context}")

    home_away, selected, opponent = _resolve_sides(scheduled, team.id, context)
    official_date = _parse_game_date(
        scheduled.official_date, "official schedule date", context
    )
    _validate_split_schedule_consistency(
        split=split,
        team_id=team.id,
        home_away=home_away,
        selected=selected,
        opponent=opponent,
        official_date=official_date,
        context=context,
    )

    try:
        return TeamGameBattingLine(
            game_pk=game_pk,
            game_date=official_date,
            season=season,
            team_id=team.id,
            team_name=team.name,
            opponent_id=opponent.team.id,
            opponent_name=opponent.team.name,
            home_away=home_away,
            hits=split.stat.hits,
            runs=split.stat.runs,
            status=status,
            game_number=scheduled.game_number,
            doubleheader=scheduled.double_header != "N",
            scheduled_innings=scheduled.scheduled_innings,
        )
    except ValidationError as exc:
        raise TeamGameDataError(f"Could not normalize {context}: {exc}") from exc


def _resolve_sides(
    scheduled: ScheduleGames,
    team_id: int,
    context: str,
) -> tuple[HomeAway, ScheduleGameTeam, ScheduleGameTeam]:
    """Derive home or away, the selected team, and the opponent from the schedule."""
    teams = scheduled.teams
    if teams is None:
        raise TeamGameDataError(f"No schedule team information for {context}")
    if teams.home.team.id == team_id:
        return "home", teams.home, teams.away
    if teams.away.team.id == team_id:
        return "away", teams.away, teams.home
    raise TeamGameDataError(
        f"Team {team_id} does not appear in the schedule entry for {context}"
    )


def _validate_split_schedule_consistency(
    *,
    split: HittingGameLog,
    team_id: int,
    home_away: HomeAway,
    selected: ScheduleGameTeam,
    opponent: ScheduleGameTeam,
    official_date: date,
    context: str,
) -> None:
    """Enforce the values the game log split and the schedule entry share.

    Team names are excluded on purpose: the game log reports the franchise's
    current name while the team lookup reports its name for the season.
    """
    if split.team is None or split.team.id != team_id:
        reported = None if split.team is None else split.team.id
        raise TeamGameDataError(
            f"Game log split reports team {reported} but team {team_id} "
            f"was requested for {context}"
        )

    split_date = _parse_game_date(split.date, "game log date", context)
    if split_date != official_date:
        raise TeamGameDataError(
            f"Game log date {split_date.isoformat()} does not match official "
            f"schedule date {official_date.isoformat()} for {context}"
        )

    if split.opponent.id != opponent.team.id:
        raise TeamGameDataError(
            f"Game log opponent {split.opponent.id} does not match scheduled "
            f"opponent {opponent.team.id} for {context}"
        )

    if split.is_home != (home_away == "home"):
        raise TeamGameDataError(
            f"Game log reports is_home={split.is_home} but the schedule places "
            f"team {team_id} {home_away} for {context}"
        )

    runs = split.stat.runs if split.stat is not None else None
    if runs is not None and selected.score is not None and runs != selected.score:
        raise TeamGameDataError(
            f"Game log runs {runs} do not match the scheduled score "
            f"{selected.score} for {context}"
        )


def _parse_game_date(raw: str, label: str, context: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise TeamGameDataError(f"Unparsable {label} {raw!r} for {context}") from exc
