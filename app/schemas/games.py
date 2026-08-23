"""Normalized schemas for team game-level results."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

HomeAway = Literal["home", "away"]

OUTS_PER_INNING = 3
OUTS_PER_NINE_INNINGS = 27


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


class TeamGamePitchingLine(BaseModel):
    """One team's pitching result in one completed MLB game.

    Innings are stored as **outs**, an integer, and never as innings pitched.
    MLB returns ``inningsPitched`` as a string in baseball notation, where
    ``'10.2'`` means ten and two-thirds innings rather than 10.2 of them.
    Parsing that as a decimal silently corrupts every rate derived from it, so
    this model does not carry the field at all. The same split provides
    ``outs`` as an exact integer — 32 for that game — and every rate here is
    derived from it.

    Only raw components are stored. ERA, WHIP, K/9, and BB/9 are calculated
    from these fields on demand rather than persisted, so a stored rate can
    never drift from the components it came from.

    Game context (opponent, status, game number, scheduled innings) is
    duplicated from the batting line rather than joined. It comes from the same
    schedule request at no extra cost, and it keeps a pitching row readable on
    its own instead of only in the presence of its batting counterpart.
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
    outs: int = Field(
        ge=0,
        description="Outs recorded by this team's pitchers. Three per inning, so "
        "a nine-inning start is 27. Never innings pitched: see the class "
        "docstring for why that field is not carried.",
    )
    hits_allowed: int = Field(ge=0, description="Hits allowed by this team.")
    runs_allowed: int = Field(ge=0, description="Runs allowed, earned or not.")
    earned_runs: int = Field(
        ge=0,
        description="Earned runs allowed. A subset of runs_allowed, so it can "
        "never exceed it.",
    )
    base_on_balls: int = Field(ge=0, description="Walks issued by this team.")
    strikeouts: int = Field(
        ge=0,
        description="Strikeouts recorded by this team's pitchers. Pitching "
        "strikeouts, which are a different statistic from the batting "
        "strikeouts stored on the batting line.",
    )
    home_runs_allowed: int = Field(
        ge=0,
        description="Home runs allowed. A subset of hits_allowed, so it can "
        "never exceed it.",
    )
    batters_faced: int = Field(ge=0, description="Batters faced by this team.")
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

    @model_validator(mode="after")
    def _components_are_subsets_of_their_totals(self) -> TeamGamePitchingLine:
        """Reject a line whose parts contradict each other.

        Both rules are definitional rather than empirical: an earned run is a
        run, and a home run is a hit. Checked across 648 real 2025 team-games
        with no violations before being encoded here.
        """
        if self.earned_runs > self.runs_allowed:
            raise ValueError(
                f"earned_runs ({self.earned_runs}) cannot exceed runs_allowed "
                f"({self.runs_allowed}); an earned run is a run"
            )
        if self.home_runs_allowed > self.hits_allowed:
            raise ValueError(
                f"home_runs_allowed ({self.home_runs_allowed}) cannot exceed "
                f"hits_allowed ({self.hits_allowed}); a home run is a hit"
            )
        if self.batters_faced < self.outs:
            raise ValueError(
                f"batters_faced ({self.batters_faced}) cannot be fewer than outs "
                f"({self.outs}); every out is recorded against a batter faced"
            )
        return self

    @property
    def innings_pitched(self) -> float:
        """Outs expressed as innings, for calculation only.

        A true fraction, not baseball notation: 32 outs is ``10.666...``, which
        is what a rate calculation needs. Use ``innings_pitched_display`` for
        the ``10.2`` form a reader expects to see.
        """
        return self.outs / OUTS_PER_INNING

    @property
    def innings_pitched_display(self) -> str:
        """Outs in the baseball notation a box score prints: ``10.2`` for 32 outs."""
        return f"{self.outs // OUTS_PER_INNING}.{self.outs % OUTS_PER_INNING}"
