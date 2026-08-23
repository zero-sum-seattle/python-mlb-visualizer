"""Builders for normalized domain records used across the offline test suite."""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from app.schemas.analytics import (
    LeagueBaserunnersContext,
    LeagueHitsContext,
    LeagueRunsContext,
    LeagueStrikeoutsContext,
)
from app.schemas.games import (
    TeamGameBattingLine,
    TeamGamePitchingLine,
    TeamGameRunResult,
)

MARINERS_ID = 136
MARINERS_NAME = "Seattle Mariners"
TWINS_ID = 142
TWINS_NAME = "Minnesota Twins"
OPENING_DAY = date(2025, 3, 27)


def make_batting_line(**overrides: Any) -> TeamGameBattingLine:
    """Build one batting line, overriding only the fields a test cares about."""
    base: dict[str, Any] = {
        "game_pk": 776000,
        "game_date": OPENING_DAY,
        "season": 2025,
        "team_id": MARINERS_ID,
        "team_name": MARINERS_NAME,
        "opponent_id": TWINS_ID,
        "opponent_name": TWINS_NAME,
        "home_away": "home",
        "hits": 8,
        "runs": 4,
        # Unset by default, matching a row persisted before Milestone 3.5.
        # Tests that need real batting strikeouts pass them explicitly.
        "strikeouts": None,
        # Unset by default, matching a row persisted before these two columns
        # existed. Tests that need real baserunner components pass them
        # explicitly.
        "base_on_balls": None,
        "hit_by_pitch": None,
        "status": "Final",
        "game_number": 1,
        "doubleheader": False,
        "scheduled_innings": 9,
    }
    base.update(overrides)
    return TeamGameBattingLine(**base)


def make_season(
    hits: Sequence[int],
    *,
    team_id: int = MARINERS_ID,
    team_name: str = MARINERS_NAME,
    season: int = 2025,
    start_date: date | None = None,
    strikeouts: Sequence[int | None] | None = None,
    runs: Sequence[int] | None = None,
    base_on_balls: Sequence[int | None] | None = None,
    hit_by_pitch: Sequence[int | None] | None = None,
) -> list[TeamGameBattingLine]:
    """Build one game per hit total, on consecutive days, in season order.

    Game ids are derived from the season so one team can hold several seasons
    without colliding on the ``(team_id, game_pk)`` unique key.

    ``strikeouts``, ``base_on_balls``, and ``hit_by_pitch`` each default to
    None for every game, which is what a row persisted before that column
    existed looks like. Pass one value per game to build a team-season that
    has been imported with that metric.

    ``runs`` defaults to the batting line's own run total for every game.
    Unlike the others there is no unset case to model: ``runs`` is required on
    every persisted record. Pass one value per game to choose the totals.
    """
    if strikeouts is not None and len(strikeouts) != len(hits):
        raise ValueError(
            f"strikeouts has {len(strikeouts)} values but hits has {len(hits)}"
        )
    if runs is not None and len(runs) != len(hits):
        raise ValueError(f"runs has {len(runs)} values but hits has {len(hits)}")
    if base_on_balls is not None and len(base_on_balls) != len(hits):
        raise ValueError(
            f"base_on_balls has {len(base_on_balls)} values but hits has {len(hits)}"
        )
    if hit_by_pitch is not None and len(hit_by_pitch) != len(hits):
        raise ValueError(
            f"hit_by_pitch has {len(hit_by_pitch)} values but hits has {len(hits)}"
        )
    opening_day = start_date or date(season, OPENING_DAY.month, OPENING_DAY.day)
    return [
        make_batting_line(
            game_pk=season * 1000 + index,
            game_date=opening_day + timedelta(days=index),
            season=season,
            team_id=team_id,
            team_name=team_name,
            home_away="home" if index % 2 == 0 else "away",
            hits=value,
            strikeouts=None if strikeouts is None else strikeouts[index],
            base_on_balls=None if base_on_balls is None else base_on_balls[index],
            hit_by_pitch=None if hit_by_pitch is None else hit_by_pitch[index],
            **({} if runs is None else {"runs": runs[index]}),
        )
        for index, value in enumerate(hits)
    ]


def make_run_result(**overrides: Any) -> TeamGameRunResult:
    """Build one paired game result, overriding only the fields a test cares about."""
    base: dict[str, Any] = {
        "game_pk": 776000,
        "game_date": OPENING_DAY,
        "season": 2025,
        "team_id": MARINERS_ID,
        "team_name": MARINERS_NAME,
        "opponent_id": TWINS_ID,
        "opponent_name": TWINS_NAME,
        "home_away": "home",
        "runs_scored": 5,
        "runs_allowed": 3,
        "game_number": 1,
    }
    base.update(overrides)
    return TeamGameRunResult(**base)


def make_run_result_season(
    runs_scored: Sequence[int],
    runs_allowed: Sequence[int],
    *,
    team_id: int = MARINERS_ID,
    team_name: str = MARINERS_NAME,
    season: int = 2025,
    start_date: date | None = None,
) -> list[TeamGameRunResult]:
    """Build one paired game per score pair, on consecutive days, in season order.

    Game ids are derived from the season the same way ``make_season`` derives
    them, so a test can build a team's batting lines and its paired results for
    the same season and have the two agree on ``game_pk``.

    Real completed games are never tied, so a tied pair is rejected here rather
    than silently producing a game that could not have happened.
    """
    if len(runs_scored) != len(runs_allowed):
        raise ValueError(
            f"runs_scored has {len(runs_scored)} values but runs_allowed has "
            f"{len(runs_allowed)}"
        )
    tied = [
        index
        for index, (scored, allowed) in enumerate(
            zip(runs_scored, runs_allowed, strict=True)
        )
        if scored == allowed
    ]
    if tied:
        raise ValueError(
            f"A completed MLB game cannot end tied, but games {tied} are tied"
        )
    opening_day = start_date or date(season, OPENING_DAY.month, OPENING_DAY.day)
    return [
        make_run_result(
            game_pk=season * 1000 + index,
            game_date=opening_day + timedelta(days=index),
            season=season,
            team_id=team_id,
            team_name=team_name,
            home_away="home" if index % 2 == 0 else "away",
            runs_scored=scored,
            runs_allowed=runs_allowed[index],
        )
        for index, scored in enumerate(runs_scored)
    ]


def make_league_hits_context(
    *,
    season: int = 2025,
    total_hits: int = 80,
    team_game_records: int = 10,
    teams_represented: int = 2,
) -> LeagueHitsContext:
    """Build MLB-wide context directly, for tests about presentation.

    Tests of the formula itself build the context from batting lines through
    ``build_league_hits_context``. Tests about cards, traces, and wording only
    need a context holding a chosen average, so they build one here.
    """
    return LeagueHitsContext(
        season=season,
        teams_represented=teams_represented,
        team_game_records=team_game_records,
        total_hits=total_hits,
        hits_per_game=total_hits / team_game_records,
    )


def make_league_strikeouts_context(
    *,
    season: int = 2025,
    total_strikeouts: int = 80,
    team_game_records: int = 10,
    teams_represented: int = 2,
) -> LeagueStrikeoutsContext:
    """Build MLB-wide batting strikeout context directly, for presentation tests.

    Tests of the formula itself build the context from batting lines through
    ``build_league_strikeouts_context``. Tests about cards, traces, and wording
    only need a context holding a chosen average, so they build one here.
    """
    return LeagueStrikeoutsContext(
        season=season,
        teams_represented=teams_represented,
        team_game_records=team_game_records,
        total_strikeouts=total_strikeouts,
        strikeouts_per_game=total_strikeouts / team_game_records,
    )


def make_league_baserunners_context(
    *,
    season: int = 2025,
    total_baserunners: int = 100,
    team_game_records: int = 10,
    teams_represented: int = 2,
) -> LeagueBaserunnersContext:
    """Build MLB-wide baserunners context directly, for presentation tests.

    Tests of the formula itself build the context from batting lines through
    ``build_league_baserunners_context``. Tests about cards, traces, and
    wording only need a context holding a chosen average, so they build one
    here.
    """
    return LeagueBaserunnersContext(
        season=season,
        teams_represented=teams_represented,
        team_game_records=team_game_records,
        total_baserunners=total_baserunners,
        baserunners_per_game=total_baserunners / team_game_records,
    )


def make_league_runs_context(
    *,
    season: int = 2025,
    total_runs: int = 45,
    team_game_records: int = 10,
    teams_represented: int = 2,
) -> LeagueRunsContext:
    """Build MLB-wide run context directly, for tests about presentation.

    Tests of the formula itself build the context from batting lines through
    ``build_league_runs_context``. Tests about cards, traces, and wording only
    need a context holding a chosen average, so they build one here.
    """
    return LeagueRunsContext(
        season=season,
        teams_represented=teams_represented,
        team_game_records=team_game_records,
        total_runs=total_runs,
        runs_per_game=total_runs / team_game_records,
    )


def make_pitching_line(**overrides: Any) -> TeamGamePitchingLine:
    """Build one pitching line, overriding only the fields a test cares about."""
    base: dict[str, Any] = {
        "game_pk": 776000,
        "game_date": OPENING_DAY,
        "season": 2025,
        "team_id": MARINERS_ID,
        "team_name": MARINERS_NAME,
        "opponent_id": TWINS_ID,
        "opponent_name": TWINS_NAME,
        "home_away": "home",
        # A regulation nine innings, so a test that cares only about earned
        # runs gets the arithmetic it expects.
        "outs": 27,
        "hits_allowed": 8,
        "runs_allowed": 3,
        "earned_runs": 3,
        "base_on_balls": 2,
        "strikeouts": 9,
        "home_runs_allowed": 1,
        "batters_faced": 38,
        "number_of_pitches": 150,
        "strikes": 98,
        "status": "Final",
        "game_number": 1,
        "doubleheader": False,
        "scheduled_innings": 9,
    }
    base.update(overrides)
    return TeamGamePitchingLine(**base)


def make_pitching_season(
    earned_runs: Sequence[int],
    *,
    outs: Sequence[int] | None = None,
    team_id: int = MARINERS_ID,
    team_name: str = MARINERS_NAME,
    season: int = 2025,
) -> list[TeamGamePitchingLine]:
    """Build one pitching game per earned-run total, on consecutive days.

    ``outs`` defaults to a regulation 27 per game. Pass it to build a season
    with short or extra-inning games, which is what the rate-aggregation tests
    need: equal outs everywhere is the one case where the correct aggregation
    and a naive mean of game rates happen to agree.
    """
    if outs is not None and len(outs) != len(earned_runs):
        raise ValueError(
            f"outs has {len(outs)} values but earned_runs has {len(earned_runs)}"
        )
    opening_day = date(season, OPENING_DAY.month, OPENING_DAY.day)
    return [
        make_pitching_line(
            game_pk=season * 1000 + index,
            game_date=opening_day + timedelta(days=index),
            season=season,
            team_id=team_id,
            team_name=team_name,
            home_away="home" if index % 2 == 0 else "away",
            outs=27 if outs is None else outs[index],
            earned_runs=value,
            # Runs allowed must cover earned runs, and batters faced must
            # cover outs, or the domain model rejects the line.
            runs_allowed=value,
            batters_faced=(27 if outs is None else outs[index]) + 11,
        )
        for index, value in enumerate(earned_runs)
    ]
