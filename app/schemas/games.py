"""Normalized schemas for team game-level results."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HomeAway = Literal["home", "away"]


class TeamGameBattingLine(BaseModel):
    """One team's batting result in one completed MLB game.

    Every field is a plain value derived from a python-mlb-statsapi return
    model. No raw MLB response objects are carried on this model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    game_pk: int = Field(gt=0, description="MLB game identifier.")
    game_date: date = Field(description="Official date the game counts against.")
    season: int = Field(gt=0, description="Season the game belongs to.")
    team_id: int = Field(gt=0, description="MLB team id of the selected team.")
    team_name: str = Field(min_length=1, description="Display name of the team.")
    opponent_id: int = Field(gt=0, description="MLB team id of the opponent.")
    opponent_name: str = Field(
        min_length=1, description="Display name of the opponent."
    )
    home_away: HomeAway = Field(
        description="Whether the selected team was home or away."
    )
    hits: int = Field(ge=0, description="Hits recorded by the selected team.")
    runs: int = Field(ge=0, description="Runs scored by the selected team.")
    strikeouts: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Times the selected team's hitters struck out. Batting strikeouts, "
            "not strikeouts recorded by its pitchers. Optional only so rows "
            "persisted before batting strikeouts were collected keep an honest "
            "unknown value; normalization of a fresh MLB response requires a "
            "real count."
        ),
    )
    base_on_balls: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Times the selected team's hitters drew a walk. Optional only so "
            "rows persisted before this metric was collected keep an honest "
            "unknown value; normalization of a fresh MLB response requires a "
            "real count."
        ),
    )
    hit_by_pitch: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Times the selected team's hitters were hit by a pitch. Optional "
            "only so rows persisted before this metric was collected keep an "
            "honest unknown value; normalization of a fresh MLB response "
            "requires a real count."
        ),
    )
    status: str = Field(min_length=1, description="Detailed MLB game status.")
    game_number: int = Field(
        ge=1,
        description="Game number on the date, 2 for the second game of a doubleheader.",
    )
    doubleheader: bool = Field(
        description="Whether the game was part of a doubleheader."
    )
    scheduled_innings: int = Field(
        ge=1,
        description="Innings the game was scheduled for, which is not always nine.",
    )
