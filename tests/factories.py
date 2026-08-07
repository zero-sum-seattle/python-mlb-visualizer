"""Builders for normalized domain records used across the offline test suite."""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

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
) -> list[TeamGameBattingLine]:
    """Build one game per hit total, on consecutive days, in season order.

    Game ids are derived from the season so one team can hold several seasons
    without colliding on the ``(team_id, game_pk)`` unique key.
    """
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
        )
        for index, value in enumerate(hits)
    ]
