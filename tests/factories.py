"""Builders for normalized domain records used across the offline test suite."""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from app.schemas.analytics import LeagueHitsContext, LeagueStrikeoutsContext
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
) -> list[TeamGameBattingLine]:
    """Build one game per hit total, on consecutive days, in season order.

    Game ids are derived from the season so one team can hold several seasons
    without colliding on the ``(team_id, game_pk)`` unique key.

    ``strikeouts`` defaults to None for every game, which is what a row
    persisted before Milestone 3.5 looks like. Pass one value per game to build
    a team-season that has been imported with batting strikeouts.
    """
    if strikeouts is not None and len(strikeouts) != len(hits):
        raise ValueError(
            f"strikeouts has {len(strikeouts)} values but hits has {len(hits)}"
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
