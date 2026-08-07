"""Schemas for calculated team hitting analytics.

These models are the contract between the analytics layer and everything that
presents it. They carry finished numbers, not raw MLB payloads, and they keep
dates as ``date`` objects so presentation can choose its own formatting.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.games import HomeAway


class TeamHitsPoint(BaseModel):
    """One completed game plotted on the team hits chart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    game_pk: int = Field(gt=0, description="MLB game identifier.")
    game_number: int = Field(
        ge=1,
        description="MLB game number on the date, 2 for the second game of a "
        "doubleheader. Used for ordering, not for the x axis.",
    )
    season_game_number: int = Field(
        ge=1,
        description="Continuous 1-based position of the game within the season.",
    )
    game_date: date = Field(description="Official date the game counts against.")
    opponent_name: str = Field(
        min_length=1, description="Display name of the opponent."
    )
    home_away: HomeAway = Field(description="Whether the team was home or away.")
    hits: int = Field(ge=0, description="Hits recorded by the team in this game.")
    rolling_average: float = Field(
        ge=0,
        description="Trailing rolling hits-per-game average ending at this game.",
    )


class TeamHitsSummary(BaseModel):
    """Headline numbers describing a team-season's hitting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    games_played: int = Field(ge=1, description="Completed games analysed.")
    season_average: float = Field(ge=0, description="Hits per game across the season.")
    recent_average: float = Field(
        ge=0,
        description="Hits per game over the most recent rolling window.",
    )
    prior_window_average: float | None = Field(
        default=None,
        ge=0,
        description="Hits per game over the window immediately before the recent "
        "one, or None when two complete windows do not exist.",
    )
    change_vs_prior_window: float | None = Field(
        default=None,
        description="recent_average - prior_window_average, or None.",
    )

    @model_validator(mode="after")
    def _prior_window_fields_agree(self) -> TeamHitsSummary:
        has_prior = self.prior_window_average is not None
        has_change = self.change_vs_prior_window is not None
        if has_prior != has_change:
            raise ValueError(
                "prior_window_average and change_vs_prior_window must both be "
                "present or both be None"
            )
        return self


class TeamHitsAnalysis(BaseModel):
    """A team-season's hitting trend, ready to chart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Historical name for the season.")
    season: int = Field(gt=0, description="Season analysed.")
    rolling_window: int = Field(ge=1, description="Games in the trailing window.")
    season_average: float = Field(ge=0, description="Hits per game across the season.")
    points: tuple[TeamHitsPoint, ...] = Field(
        min_length=1, description="Games in chart order."
    )
    summary: TeamHitsSummary

    @model_validator(mode="after")
    def _summary_matches_points(self) -> TeamHitsAnalysis:
        if self.summary.games_played != len(self.points):
            raise ValueError(
                "summary.games_played must equal the number of chart points"
            )
        return self

    @property
    def last_game_date(self) -> date:
        """Date of the most recent completed game in the analysis."""
        return self.points[-1].game_date
