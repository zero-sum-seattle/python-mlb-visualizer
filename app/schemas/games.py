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


class TeamGameRunResult(BaseModel):
    """One completed game seen from both sides: runs scored and runs allowed.

    Built by pairing a team's stored batting line with the opponent's stored
    batting line for the same ``game_pk``. Runs allowed is not a figure the
    MLB API is asked for; it is the opponent's own runs scored, which is the
    same number.

    Only games where both rows are stored can be represented. A team-season
    imported on its own has no opponent rows, and the repository reports those
    games as unpaired rather than inventing a zero.
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
    runs_scored: int = Field(ge=0, description="Runs scored by the selected team.")
    runs_allowed: int = Field(
        ge=0,
        description="Runs scored by the opponent, which is this team's runs allowed.",
    )
    game_number: int = Field(
        ge=1,
        description="Game number on the date, 2 for the second game of a doubleheader.",
    )

    @property
    def run_differential(self) -> int:
        """Runs scored minus runs allowed for this game."""
        return self.runs_scored - self.runs_allowed

    @property
    def is_win(self) -> bool:
        """Whether the selected team won.

        A completed MLB game cannot end tied, so outscoring the opponent is
        the whole definition. Games that never reached a final are not stored
        as completed team-game records in the first place.
        """
        return self.runs_scored > self.runs_allowed


class TeamSeasonRunResults(BaseModel):
    """Every game of a team-season that could be paired, and those that could not.

    Reporting the unpaired games rather than silently dropping them is what
    lets the analytics layer refuse to describe a partial season. Dropping
    them would understate runs allowed and produce a run differential that
    looks plausible and is wrong.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: tuple[TeamGameRunResult, ...] = Field(
        description="Games where both teams' batting lines are stored, in chart order."
    )
    unpaired_game_pks: tuple[int, ...] = Field(
        description="Games of this team-season with no stored opponent row."
    )
