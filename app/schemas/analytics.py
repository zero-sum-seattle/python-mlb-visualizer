"""Schemas for calculated team hitting analytics.

These models are the contract between the analytics layer and everything that
presents it. They carry finished numbers, not raw MLB payloads, and they keep
dates as ``date`` objects so presentation can choose its own formatting.

Hits and batting strikeouts are modelled separately rather than through a
shared metric type. They are read the same way but mean different things, and
one honest duplication is cheaper to follow than an abstraction covering two
cases.
"""

from __future__ import annotations

from datetime import date
from math import isclose

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
    """Headline numbers describing a team-season's hitting.

    ``season_average`` is the single authoritative season average; the chart's
    reference line and the summary card both read it from here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    games_played: int = Field(ge=1, description="Completed games analysed.")
    season_average: float = Field(
        ge=0, description="Hits per game across the stored completed games."
    )
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


class TeamStrikeoutsPoint(BaseModel):
    """One completed game plotted on the team batting strikeout chart."""

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
    strikeouts: int = Field(
        ge=0,
        description="Times the team's hitters struck out in this game. Never "
        "None: a game with an unknown total is refused rather than plotted.",
    )
    rolling_average: float = Field(
        ge=0,
        description="Trailing rolling batting-strikeouts-per-game average "
        "ending at this game.",
    )


class TeamStrikeoutsSummary(BaseModel):
    """Headline numbers describing a team-season's batting strikeouts.

    ``season_average`` is the single authoritative season average; the chart's
    reference line and the summary card both read it from here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    games_played: int = Field(ge=1, description="Completed games analysed.")
    season_average: float = Field(
        ge=0,
        description="Batting strikeouts per game across the stored completed games.",
    )
    recent_average: float = Field(
        ge=0,
        description="Batting strikeouts per game over the most recent rolling window.",
    )
    prior_window_average: float | None = Field(
        default=None,
        ge=0,
        description="Batting strikeouts per game over the window immediately "
        "before the recent one, or None when two complete windows do not exist.",
    )
    change_vs_prior_window: float | None = Field(
        default=None,
        description="recent_average - prior_window_average, or None. A positive "
        "value means more batting strikeouts, which is not automatically better "
        "or worse; direction is left to the reader.",
    )

    @model_validator(mode="after")
    def _prior_window_fields_agree(self) -> TeamStrikeoutsSummary:
        has_prior = self.prior_window_average is not None
        has_change = self.change_vs_prior_window is not None
        if has_prior != has_change:
            raise ValueError(
                "prior_window_average and change_vs_prior_window must both be "
                "present or both be None"
            )
        return self


class TeamStrikeoutsAnalysis(BaseModel):
    """A team-season's batting strikeout trend, ready to chart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Historical name for the season.")
    season: int = Field(gt=0, description="Season analysed.")
    rolling_window: int = Field(ge=1, description="Games in the trailing window.")
    points: tuple[TeamStrikeoutsPoint, ...] = Field(
        min_length=1, description="Games in chart order."
    )
    summary: TeamStrikeoutsSummary

    @model_validator(mode="after")
    def _summary_matches_points(self) -> TeamStrikeoutsAnalysis:
        if self.summary.games_played != len(self.points):
            raise ValueError(
                "summary.games_played must equal the number of chart points"
            )
        return self

    @property
    def last_game_date(self) -> date:
        """Date of the most recent completed game in the analysis."""
        return self.points[-1].game_date


class LeagueHitsContext(BaseModel):
    """MLB-wide hitting context for one season.

    Built from every persisted team-game batting line for the season, so
    ``hits_per_game`` is a game-weighted mean across team-game records rather
    than the unweighted mean of each club's own average. Teams do not all play
    the same number of games, so those two numbers are not the same statistic.

    Every field describes the games **currently stored** for the season. For a
    season still being played that is the completed games held by the most
    recent complete league-wide refresh, not a whole season.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    season: int = Field(gt=0, description="Season the context describes.")
    teams_represented: int = Field(
        ge=1,
        description="Distinct teams with at least one stored game in the season.",
    )
    team_game_records: int = Field(
        ge=1,
        description="Team-game batting lines counted. One MLB game contributes "
        "two records once both clubs are stored, so this is not a game count.",
    )
    total_hits: int = Field(
        ge=0, description="Hits summed across every counted team-game record."
    )
    hits_per_game: float = Field(
        ge=0,
        description="total_hits / team_game_records.",
    )

    @model_validator(mode="after")
    def _hits_per_game_matches_the_totals(self) -> LeagueHitsContext:
        expected = self.total_hits / self.team_game_records
        if not isclose(self.hits_per_game, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"hits_per_game ({self.hits_per_game}) must equal total_hits / "
                f"team_game_records ({expected})"
            )
        if self.teams_represented > self.team_game_records:
            raise ValueError(
                f"teams_represented ({self.teams_represented}) cannot exceed "
                f"team_game_records ({self.team_game_records})"
            )
        return self


class TeamHitsLeagueComparison(BaseModel):
    """One team's hits per game placed beside MLB overall for the same season.

    Purely descriptive. A difference here says the selected team averaged more
    or fewer hits per game than MLB across the stored season; it carries no
    claim of significance, of skill, or of anything predictive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id of the selected team.")
    team_name: str = Field(min_length=1, description="Name for the season.")
    season: int = Field(gt=0, description="Season compared.")
    team_hits_per_game: float = Field(
        ge=0,
        description="The selected team's average across its stored games, taken "
        "from TeamHitsSummary.season_average so the page cannot disagree with "
        "itself.",
    )
    league: LeagueHitsContext = Field(description="MLB-wide context compared against.")
    difference_vs_mlb: float = Field(
        description="team_hits_per_game - league.hits_per_game. Positive means "
        "the team averaged more hits per game than MLB overall.",
    )

    @model_validator(mode="after")
    def _comparison_is_internally_consistent(self) -> TeamHitsLeagueComparison:
        if self.season != self.league.season:
            raise ValueError(
                f"season ({self.season}) must match the league context season "
                f"({self.league.season})"
            )
        expected = self.team_hits_per_game - self.league.hits_per_game
        if not isclose(self.difference_vs_mlb, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"difference_vs_mlb ({self.difference_vs_mlb}) must equal "
                f"team_hits_per_game - league.hits_per_game ({expected})"
            )
        return self


class LeagueStrikeoutsContext(BaseModel):
    """MLB-wide batting strikeout context for one season.

    Built from every persisted team-game batting line for the season, so
    ``strikeouts_per_game`` is a game-weighted mean across team-game records
    rather than the unweighted mean of each club's own average. Teams do not
    all play the same number of games, so those two numbers are not the same
    statistic.

    Every counted record carries a known batting strikeout total. A season
    holding even one record with an unknown total cannot produce this context
    at all, because an average over the rows that happen to have a value is not
    an MLB-wide average.

    Every field describes the games **currently stored** for the season. For a
    season still being played that is the completed games held by the most
    recent complete league-wide refresh, not a whole season.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    season: int = Field(gt=0, description="Season the context describes.")
    teams_represented: int = Field(
        ge=1,
        description="Distinct teams with at least one stored game in the season.",
    )
    team_game_records: int = Field(
        ge=1,
        description="Team-game batting lines counted. One MLB game contributes "
        "two records once both clubs are stored, so this is not a game count.",
    )
    total_strikeouts: int = Field(
        ge=0,
        description="Batting strikeouts summed across every counted team-game "
        "record. Never includes a substituted value for an unknown total.",
    )
    strikeouts_per_game: float = Field(
        ge=0,
        description="total_strikeouts / team_game_records.",
    )

    @model_validator(mode="after")
    def _strikeouts_per_game_matches_the_totals(self) -> LeagueStrikeoutsContext:
        expected = self.total_strikeouts / self.team_game_records
        if not isclose(self.strikeouts_per_game, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"strikeouts_per_game ({self.strikeouts_per_game}) must equal "
                f"total_strikeouts / team_game_records ({expected})"
            )
        if self.teams_represented > self.team_game_records:
            raise ValueError(
                f"teams_represented ({self.teams_represented}) cannot exceed "
                f"team_game_records ({self.team_game_records})"
            )
        return self


class TeamStrikeoutsLeagueComparison(BaseModel):
    """One team's batting K/Game placed beside MLB overall for the same season.

    Purely descriptive. A difference here says the selected team's hitters
    struck out more or fewer times per game than MLB across the stored season;
    it carries no claim of significance, and neither direction is labelled good
    or bad. Striking out less often is not automatically better hitting, and a
    club that strikes out more may be doing other things well.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id of the selected team.")
    team_name: str = Field(min_length=1, description="Name for the season.")
    season: int = Field(gt=0, description="Season compared.")
    team_strikeouts_per_game: float = Field(
        ge=0,
        description="The selected team's average across its stored games, taken "
        "from TeamStrikeoutsSummary.season_average so the page cannot disagree "
        "with itself.",
    )
    league: LeagueStrikeoutsContext = Field(
        description="MLB-wide context compared against."
    )
    difference_vs_mlb: float = Field(
        description="team_strikeouts_per_game - league.strikeouts_per_game. "
        "Positive means the team's hitters struck out more times per game than "
        "MLB overall, negative fewer.",
    )

    @model_validator(mode="after")
    def _comparison_is_internally_consistent(self) -> TeamStrikeoutsLeagueComparison:
        if self.season != self.league.season:
            raise ValueError(
                f"season ({self.season}) must match the league context season "
                f"({self.league.season})"
            )
        expected = self.team_strikeouts_per_game - self.league.strikeouts_per_game
        if not isclose(self.difference_vs_mlb, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"difference_vs_mlb ({self.difference_vs_mlb}) must equal "
                f"team_strikeouts_per_game - league.strikeouts_per_game "
                f"({expected})"
            )
        return self
