"""Retrieve and normalize a team's game-level hitting and pitching results.

Hitting numbers come from the team ``gameLog`` hitting stat type, which returns
one split per played game in a single request. Hits, runs, batting strikeouts,
walks, and hit-by-pitch all arrive in that same split, so no additional MLB
request is made for any of them. Game context that the stat split does not
carry (status, opponent name, game number, scheduled innings) comes from a
single team schedule request and is joined on ``gamePk``.

Pitching numbers come from the same ``gameLog`` stat type in the ``pitching``
stat group, which **is** a separate request — unlike the hitting components,
they do not ride along with the hits and runs. Both game logs join to the same
schedule the same way, so the retrieval, validation, and normalization helpers
below are shared rather than written twice.

The two logs are validated against opposite sides of the score. A hitting
split's runs must equal the selected team's scheduled score; a pitching split's
runs are runs *allowed*, so they must equal the opponent's. That makes the
schedule an independent check on which group a split really belongs to.

The two sources overlap on team, opponent, home/away, date, and runs. That
overlap is validated for every game rather than assumed, so an upstream or
package-model change surfaces as a ``TeamGameDataError`` instead of a silently
wrong record.

Both directions of that join are checked. Every game log split must have a
schedule entry, and — just as importantly — every completed game in the
schedule must end up with a normalized batting line. The reverse direction is
what catches a split that never reached this code: ``python-mlb-statsapi``
discards a stat split whose raw stat object is empty, which would otherwise
leave a completed game silently absent from an otherwise healthy-looking
team-season. A team-season missing a completed game is refused rather than
returned short, because a caller cannot tell a short season from a real one.

See ``docs/team-game-data-spike.md`` for the investigation behind this choice.
"""

from datetime import date
from typing import Literal, Protocol

from mlbstatsapi import Mlb
from mlbstatsapi.exceptions import TheMlbStatsApiException
from mlbstatsapi.models.schedules import Schedule, ScheduleGames, ScheduleGameTeam
from mlbstatsapi.models.stats import HittingGameLog, PitchingGameLog
from mlbstatsapi.models.teams import Team
from pydantic import ValidationError

from app.schemas.games import HomeAway, TeamGameBattingLine, TeamGamePitchingLine

MLB_SPORT_ID = 1
REGULAR_SEASON_GAME_TYPE = "R"
HITTING_STAT_GROUP = "hitting"
PITCHING_STAT_GROUP = "pitching"
GAME_LOG_STAT_TYPE = "gameLog"

# Either stat group's game log split. Both carry the team, date, opponent,
# is_home, and stat fields the shared validation below reads.
GameLogSplit = HittingGameLog | PitchingGameLog
TeamGameLine = TeamGameBattingLine | TeamGamePitchingLine

# Whose scheduled score a split's ``runs`` must equal. Hitting reports runs
# scored by the selected team; pitching reports runs allowed, which is the
# opponent's score.
RunsSide = Literal["selected", "opponent"]

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
    scheduled_games = _index_schedule_games(_fetch_schedule(client, team_id, season))
    return _normalize_batting_log(
        _fetch_hitting_game_log(client, team_id, season),
        team=team,
        season=season,
        scheduled_games=scheduled_games,
    )


def _normalize_batting_log(
    game_log: list[HittingGameLog],
    *,
    team: Team,
    season: int,
    scheduled_games: dict[int, ScheduleGames],
) -> list[TeamGameBattingLine]:
    """Join a hitting game log to the schedule and normalize every completed game."""
    lines: dict[int, TeamGameBattingLine] = {}
    for split in game_log:
        game_pk = split.game.game_pk
        scheduled = scheduled_games.get(game_pk)
        if scheduled is None:
            raise TeamGameDataError(
                f"Game {game_pk} on {split.date} is in the hitting game log for "
                f"team {team.id} but not in the {season} regular-season schedule"
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

    _require_every_completed_scheduled_game(
        scheduled_games=scheduled_games,
        normalized=lines,
        team_id=team.id,
        season=season,
    )

    return sorted(
        lines.values(),
        key=lambda line: (line.game_date, line.game_number, line.game_pk),
    )


def get_team_game_lines(
    team_id: int,
    season: int,
    *,
    client: MlbGameDataClient | None = None,
) -> tuple[list[TeamGameBattingLine], list[TeamGamePitchingLine]]:
    """Return both the batting and pitching lines for a team-season.

    Fetching them together costs **four** MLB requests rather than the six that
    calling the two single-group functions in turn would: the team lookup and
    the season schedule are shared, and only the two game logs are group
    specific. Over a 30-club league import that is 60 requests saved.

    Both game logs are validated against the same schedule index, so a game
    present in one log and absent from the other is caught by the existing
    completed-game check on each side rather than producing two seasons of
    different lengths.
    """
    if client is not None:
        return _collect_both_lines(client, team_id, season)
    with Mlb() as owned_client:
        return _collect_both_lines(owned_client, team_id, season)


def _collect_both_lines(
    client: MlbGameDataClient,
    team_id: int,
    season: int,
) -> tuple[list[TeamGameBattingLine], list[TeamGamePitchingLine]]:
    team = _fetch_mlb_team(client, team_id, season)
    scheduled_games = _index_schedule_games(_fetch_schedule(client, team_id, season))
    batting = _normalize_batting_log(
        _fetch_hitting_game_log(client, team_id, season),
        team=team,
        season=season,
        scheduled_games=scheduled_games,
    )
    pitching = _normalize_pitching_log(
        _fetch_pitching_game_log(client, team_id, season),
        team=team,
        season=season,
        scheduled_games=scheduled_games,
    )
    return batting, pitching


def get_team_game_pitching_lines(
    team_id: int,
    season: int,
    *,
    client: MlbGameDataClient | None = None,
) -> list[TeamGamePitchingLine]:
    """Return the team's pitching line for every completed regular-season game.

    Records are sorted by game date, then game number, then game id. Both games
    of a doubleheader are returned as separate records.

    This makes its own ``gameLog`` request in the ``pitching`` stat group, plus
    the same schedule request the hitting log uses. Unlike the hitting
    components, pitching does not ride along with the hits and runs.

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
        return _collect_pitching_lines(client, team_id, season)
    with Mlb() as owned_client:
        return _collect_pitching_lines(owned_client, team_id, season)


def _collect_pitching_lines(
    client: MlbGameDataClient,
    team_id: int,
    season: int,
) -> list[TeamGamePitchingLine]:
    team = _fetch_mlb_team(client, team_id, season)
    scheduled_games = _index_schedule_games(_fetch_schedule(client, team_id, season))
    return _normalize_pitching_log(
        _fetch_pitching_game_log(client, team_id, season),
        team=team,
        season=season,
        scheduled_games=scheduled_games,
    )


def _normalize_pitching_log(
    game_log: list[PitchingGameLog],
    *,
    team: Team,
    season: int,
    scheduled_games: dict[int, ScheduleGames],
) -> list[TeamGamePitchingLine]:
    """Join a pitching game log to the schedule and normalize every completed game."""
    lines: dict[int, TeamGamePitchingLine] = {}
    for split in game_log:
        game_pk = split.game.game_pk
        scheduled = scheduled_games.get(game_pk)
        if scheduled is None:
            raise TeamGameDataError(
                f"Game {game_pk} on {split.date} is in the pitching game log for "
                f"team {team.id} but not in the {season} regular-season schedule"
            )
        status = scheduled.status
        if status is None or status.coded_game_state not in COMPLETED_CODED_GAME_STATES:
            continue
        _store_or_validate_duplicate(
            lines,
            _normalize_pitching_line(
                split=split,
                scheduled=scheduled,
                status=status.detailed_state,
                team=team,
                season=season,
            ),
        )

    _require_every_completed_scheduled_game(
        scheduled_games=scheduled_games,
        normalized=lines,
        team_id=team.id,
        season=season,
        log_name="pitching",
    )

    return sorted(
        lines.values(),
        key=lambda line: (line.game_date, line.game_number, line.game_pk),
    )


def _normalize_pitching_line(
    *,
    split: PitchingGameLog,
    scheduled: ScheduleGames,
    status: str | None,
    team: Team,
    season: int,
) -> TeamGamePitchingLine:
    game_pk = split.game.game_pk
    context = f"game {game_pk} on {split.date} for team {team.id}"

    if split.stat is None:
        raise TeamGameDataError(f"No pitching stat line returned for {context}")

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
        # A pitching split's runs are runs allowed, so the opponent's score is
        # what they must equal.
        runs_side="opponent",
    )

    try:
        return TeamGamePitchingLine(
            game_pk=game_pk,
            game_date=official_date,
            season=season,
            team_id=team.id,
            team_name=team.name,
            opponent_id=opponent.team.id,
            opponent_name=opponent.team.name,
            home_away=home_away,
            # ``outs`` rather than ``inningsPitched``: MLB returns the latter
            # as a string in baseball notation where "10.2" means ten and
            # two-thirds innings. See TeamGamePitchingLine for the full note.
            outs=_require_nonnegative_stat(
                split.stat.outs, field="outs", context=context, log_name="pitching"
            ),
            hits_allowed=_require_nonnegative_stat(
                split.stat.hits, field="hits", context=context, log_name="pitching"
            ),
            runs_allowed=_require_nonnegative_stat(
                split.stat.runs, field="runs", context=context, log_name="pitching"
            ),
            earned_runs=_require_nonnegative_stat(
                split.stat.earned_runs,
                field="earnedRuns",
                context=context,
                log_name="pitching",
            ),
            base_on_balls=_require_nonnegative_stat(
                split.stat.base_on_balls,
                field="baseOnBalls",
                context=context,
                log_name="pitching",
            ),
            strikeouts=_require_nonnegative_stat(
                split.stat.strikeouts,
                field="strikeOuts",
                context=context,
                log_name="pitching",
            ),
            home_runs_allowed=_require_nonnegative_stat(
                split.stat.home_runs,
                field="homeRuns",
                context=context,
                log_name="pitching",
            ),
            batters_faced=_require_nonnegative_stat(
                split.stat.batters_faced,
                field="battersFaced",
                context=context,
                log_name="pitching",
            ),
            number_of_pitches=_require_nonnegative_stat(
                split.stat.number_of_pitches,
                field="numberOfPitches",
                context=context,
                log_name="pitching",
            ),
            # ``balls`` is deliberately not read: MLB leaves it empty on the
            # team game log, and it is numberOfPitches - strikes.
            strikes=_require_nonnegative_stat(
                split.stat.strikes,
                field="strikes",
                context=context,
                log_name="pitching",
            ),
            status=status,
            game_number=scheduled.game_number,
            doubleheader=scheduled.double_header != "N",
            scheduled_innings=scheduled.scheduled_innings,
        )
    except ValidationError as exc:
        raise TeamGameDataError(f"Could not normalize {context}: {exc}") from exc


def _fetch_pitching_game_log(
    client: MlbGameDataClient,
    team_id: int,
    season: int,
) -> list[PitchingGameLog]:
    try:
        stat_groups = client.get_team_stats(
            team_id,
            stats=[GAME_LOG_STAT_TYPE],
            groups=[PITCHING_STAT_GROUP],
            season=season,
            gameType=REGULAR_SEASON_GAME_TYPE,
        )
    except TheMlbStatsApiException as exc:
        raise TeamGameLogError("Unable to retrieve MLB game data") from exc

    try:
        game_log = stat_groups[PITCHING_STAT_GROUP][GAME_LOG_STAT_TYPE]
    except (KeyError, TypeError) as exc:
        raise TeamGameDataError(
            f"No regular-season pitching game log returned for team {team_id} "
            f"in {season}"
        ) from exc

    splits = game_log.splits or []
    if not splits:
        raise TeamGameDataError(
            f"The regular-season pitching game log for team {team_id} in {season} "
            f"is empty"
        )
    for split in splits:
        if not isinstance(split, PitchingGameLog):
            raise TeamGameDataError(
                f"Unexpected {type(split).__name__} split in the pitching game log "
                f"for team {team_id} in {season}"
            )
    return splits


def _require_every_completed_scheduled_game(
    *,
    scheduled_games: dict[int, ScheduleGames],
    normalized: dict[int, TeamGameLine],
    team_id: int,
    season: int,
    log_name: str = "hitting",
) -> None:
    """Refuse a team-season whose game log is missing a completed game.

    Walking the game log proves every split has a schedule entry. This proves
    the reverse: that every completed regular-season game the schedule lists is
    represented by exactly one normalized batting line.

    ``scheduled_games`` is already deduplicated by ``gamePk``, keeping the
    completed row when a postponed or suspended game also appears under its
    original date, so a made-up or resumed game counts once rather than twice.

    A missing game cannot be filled in: the batting line simply was not
    returned. Recording zeros would fabricate a game nobody played that way,
    and returning the season one game short would let league coverage be marked
    COMPLETE over an incomplete dataset. Both are worse than failing.
    """
    missing = sorted(
        game_pk
        for game_pk, game in scheduled_games.items()
        if _is_completed(game) and game_pk not in normalized
    )
    if not missing:
        return

    listed = ", ".join(str(game_pk) for game_pk in missing)
    plural = "s" if len(missing) > 1 else ""
    raise TeamGameDataError(
        f"{len(missing)} completed regular-season game{plural} for team "
        f"{team_id} in {season} {'are' if len(missing) > 1 else 'is'} in the "
        f"schedule but absent from the {log_name} game log: {listed}. The "
        f"team-season is incomplete and was not returned."
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
    lines: dict[int, TeamGameLine],
    line: TeamGameLine,
) -> None:
    """Store a normalized line, rejecting a duplicate that disagrees with it.

    Either game log is expected to hold one split per game. An identical
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
            strikeouts=_require_batting_strikeouts(split, context),
            base_on_balls=_require_nonnegative_stat(
                split.stat.base_on_balls, field="baseOnBalls", context=context
            ),
            hit_by_pitch=_require_nonnegative_stat(
                split.stat.hit_by_pitch, field="hitByPitch", context=context
            ),
            status=status,
            game_number=scheduled.game_number,
            doubleheader=scheduled.double_header != "N",
            scheduled_innings=scheduled.scheduled_innings,
        )
    except ValidationError as exc:
        raise TeamGameDataError(f"Could not normalize {context}: {exc}") from exc


def _require_batting_strikeouts(split: HittingGameLog, context: str) -> int:
    """Return the team's batting strikeouts for a game, or refuse the record.

    ``SimpleHittingSplit.strikeouts`` carries MLB's ``strikeOuts`` field and is
    optional on the upstream model, so a completed game with no value means the
    payload changed or is incomplete. That is a data-integrity failure rather
    than a game in which nobody struck out, so it is raised instead of being
    read as zero.
    """
    strikeouts = split.stat.strikeouts
    if strikeouts is None:
        raise TeamGameDataError(
            f"No batting strikeouts (strikeOuts) in the hitting game log for {context}"
        )
    # ``bool`` is an ``int`` subclass, so it is rejected explicitly rather than
    # being counted as 0 or 1 strikeouts.
    if isinstance(strikeouts, bool) or not isinstance(strikeouts, int):
        raise TeamGameDataError(
            f"Batting strikeouts (strikeOuts) {strikeouts!r} is not an integer "
            f"for {context}"
        )
    if strikeouts < 0:
        raise TeamGameDataError(
            f"Batting strikeouts (strikeOuts) {strikeouts} is negative for {context}"
        )
    return strikeouts


def _require_nonnegative_stat(
    value: object, *, field: str, context: str, log_name: str = "hitting"
) -> int:
    """Return one integer stat for a game, or refuse the record.

    Shared by every optional counting stat on both game logs. All of them are
    optional on the upstream models, and a completed game with no value means
    the payload changed or is incomplete, not that the value was really zero.
    """
    if value is None:
        raise TeamGameDataError(f"No {field} in the {log_name} game log for {context}")
    # ``bool`` is an ``int`` subclass, so it is rejected explicitly rather than
    # being counted as 0 or 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TeamGameDataError(f"{field} {value!r} is not an integer for {context}")
    if value < 0:
        raise TeamGameDataError(f"{field} {value} is negative for {context}")
    return value


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
    split: GameLogSplit,
    team_id: int,
    home_away: HomeAway,
    selected: ScheduleGameTeam,
    opponent: ScheduleGameTeam,
    official_date: date,
    context: str,
    runs_side: RunsSide = "selected",
) -> None:
    """Enforce the values the game log split and the schedule entry share.

    Team names are excluded on purpose: the game log reports the franchise's
    current name while the team lookup reports its name for the season.

    ``runs_side`` says whose scheduled score the split's ``runs`` must equal.
    A hitting split reports runs scored, so it is checked against the selected
    team; a pitching split reports runs allowed, so it is checked against the
    opponent. Defaulting to the selected team keeps the hitting call unchanged.
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

    scored_by = opponent if runs_side == "opponent" else selected
    runs = split.stat.runs if split.stat is not None else None
    if runs is not None and scored_by.score is not None and runs != scored_by.score:
        side = "opponent's" if runs_side == "opponent" else "scheduled"
        raise TeamGameDataError(
            f"Game log runs {runs} do not match the {side} score "
            f"{scored_by.score} for {context}"
        )


def _parse_game_date(raw: str, label: str, context: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise TeamGameDataError(f"Unparsable {label} {raw!r} for {context}") from exc
