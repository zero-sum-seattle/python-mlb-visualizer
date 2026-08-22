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
from app.schemas.games import TeamGameBattingLine

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
