"""Schemas for calculated team hitting analytics.

These models are the contract between the analytics layer and everything that
presents it. They carry finished numbers, not raw MLB payloads, and they keep
dates as ``date`` objects so presentation can choose its own formatting.

Hits, batting strikeouts, runs, and baserunners are modelled separately rather
than through a shared metric type. They are read the same way but mean
different things, and honest duplication is cheaper to follow than an
abstraction covering four cases.
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


class TeamHittingComparisonPoint(BaseModel):
    """Normalized rolling hits and batting strikeouts for one completed game.

    Both indexes use their own MLB per-game average as the 100 baseline. The
    values are descriptive: an index above 100 means more of that statistic
    than the MLB baseline, which is not automatically favourable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    game_pk: int = Field(gt=0, description="MLB game identifier.")
    season_game_number: int = Field(
        ge=1,
        description="Continuous 1-based position of the game within the season.",
    )
    game_date: date = Field(description="Official date the game counts against.")
    opponent_name: str = Field(
        min_length=1, description="Display name of the opponent."
    )
    hits_index: float = Field(
        ge=0,
        description="Rolling team Hits/Game divided by MLB Hits/Game, times 100.",
    )
    strikeouts_index: float = Field(
        ge=0,
        description="Rolling team batting K/Game divided by MLB batting K/Game, "
        "times 100.",
    )


class TeamHittingComparisonSummary(BaseModel):
    """Headline values for the normalized hitting comparison.

    ``trend_gap`` is only the arithmetic difference between the two most
    recent normalized indexes. It is not an overall offensive-performance
    statistic, ranking, percentile, or causal claim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    games_played: int = Field(ge=1, description="Completed games analysed.")
    recent_hits_index: float = Field(
        ge=0, description="Hits index at the most recent rolling point."
    )
    recent_strikeouts_index: float = Field(
        ge=0,
        description="Batting strikeout index at the most recent rolling point.",
    )
    trend_gap: float = Field(description="recent_hits_index - recent_strikeouts_index.")

    @model_validator(mode="after")
    def _trend_gap_matches_recent_indexes(self) -> TeamHittingComparisonSummary:
        expected = self.recent_hits_index - self.recent_strikeouts_index
        if not isclose(self.trend_gap, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"trend_gap ({self.trend_gap}) must equal recent_hits_index - "
                f"recent_strikeouts_index ({expected})"
            )
        return self


class TeamHittingComparisonAnalysis(BaseModel):
    """A team-season's normalized rolling hits-versus-strikeouts trend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Historical name for the season.")
    season: int = Field(gt=0, description="Season analysed.")
    rolling_window: int = Field(ge=1, description="Games in the trailing window.")
    mlb_hits_per_game: float = Field(
        gt=0, description="Positive MLB Hits/Game denominator used for every point."
    )
    mlb_strikeouts_per_game: float = Field(
        gt=0,
        description="Positive MLB batting K/Game denominator used for every point.",
    )
    baseline_index: float = Field(
        default=100.0,
        description="MLB average on both normalized index scales. Always 100.",
    )
    points: tuple[TeamHittingComparisonPoint, ...] = Field(
        min_length=1, description="Games in chart order."
    )
    summary: TeamHittingComparisonSummary

    @model_validator(mode="after")
    def _comparison_is_internally_consistent(
        self,
    ) -> TeamHittingComparisonAnalysis:
        if not isclose(self.baseline_index, 100.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("baseline_index must equal 100")
        if self.summary.games_played != len(self.points):
            raise ValueError(
                "summary.games_played must equal the number of chart points"
            )

        recent = self.points[-1]
        if not isclose(
            self.summary.recent_hits_index,
            recent.hits_index,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "summary.recent_hits_index must equal the final point's hits_index"
            )
        if not isclose(
            self.summary.recent_strikeouts_index,
            recent.strikeouts_index,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "summary.recent_strikeouts_index must equal the final point's "
                "strikeouts_index"
            )
        return self

    @property
    def last_game_date(self) -> date:
        """Date of the most recent completed game in the comparison."""
        return self.points[-1].game_date


class TeamRunsPoint(BaseModel):
    """One completed game plotted on the team runs chart."""

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
    runs: int = Field(
        ge=0,
        description="Runs scored by the team in this game. Runs scored, not runs "
        "allowed, and never a run differential.",
    )
    rolling_average: float = Field(
        ge=0,
        description="Trailing rolling runs-per-game average ending at this game.",
    )


class TeamRunsSummary(BaseModel):
    """Headline numbers describing a team-season's run scoring.

    ``season_average`` is the single authoritative season average; the chart's
    reference line, the summary card, and the MLB comparison all read it from
    here, so the page cannot show two different team averages.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    games_played: int = Field(ge=1, description="Completed games analysed.")
    season_average: float = Field(
        ge=0, description="Runs per game across the stored completed games."
    )
    recent_average: float = Field(
        ge=0,
        description="Runs per game over the most recent rolling window.",
    )
    prior_window_average: float | None = Field(
        default=None,
        ge=0,
        description="Runs per game over the window immediately before the recent "
        "one, or None when two complete windows do not exist.",
    )
    change_vs_prior_window: float | None = Field(
        default=None,
        description="recent_average - prior_window_average, or None.",
    )

    @model_validator(mode="after")
    def _prior_window_fields_agree(self) -> TeamRunsSummary:
        has_prior = self.prior_window_average is not None
        has_change = self.change_vs_prior_window is not None
        if has_prior != has_change:
            raise ValueError(
                "prior_window_average and change_vs_prior_window must both be "
                "present or both be None"
            )
        return self


class TeamRunsAnalysis(BaseModel):
    """A team-season's run-scoring trend, ready to chart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Historical name for the season.")
    season: int = Field(gt=0, description="Season analysed.")
    rolling_window: int = Field(ge=1, description="Games in the trailing window.")
    points: tuple[TeamRunsPoint, ...] = Field(
        min_length=1, description="Games in chart order."
    )
    summary: TeamRunsSummary

    @model_validator(mode="after")
    def _summary_matches_points(self) -> TeamRunsAnalysis:
        if self.summary.games_played != len(self.points):
            raise ValueError(
                "summary.games_played must equal the number of chart points"
            )
        return self

    @property
    def last_game_date(self) -> date:
        """Date of the most recent completed game in the analysis."""
        return self.points[-1].game_date


class LeagueRunsContext(BaseModel):
    """MLB-wide run-scoring context for one season.

    Built from every persisted team-game batting line for the season, so
    ``runs_per_game`` is a game-weighted mean across team-game records rather
    than the unweighted mean of each club's own average. Teams do not all play
    the same number of games, so those two numbers are not the same statistic.

    ``runs`` is required on every persisted team-game record, so unlike batting
    strikeouts there is no unknown-total case to guard against here.

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
    total_runs: int = Field(
        ge=0, description="Runs summed across every counted team-game record."
    )
    runs_per_game: float = Field(
        ge=0,
        description="total_runs / team_game_records.",
    )

    @model_validator(mode="after")
    def _runs_per_game_matches_the_totals(self) -> LeagueRunsContext:
        expected = self.total_runs / self.team_game_records
        if not isclose(self.runs_per_game, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"runs_per_game ({self.runs_per_game}) must equal total_runs / "
                f"team_game_records ({expected})"
            )
        if self.teams_represented > self.team_game_records:
            raise ValueError(
                f"teams_represented ({self.teams_represented}) cannot exceed "
                f"team_game_records ({self.team_game_records})"
            )
        return self


class TeamRunsLeagueComparison(BaseModel):
    """One team's runs per game placed beside MLB overall for the same season.

    Purely descriptive. A difference here says the selected team scored more or
    fewer runs per game than MLB across the stored season; it carries no claim
    of significance, no park or opponent adjustment, and nothing about the runs
    the team allowed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id of the selected team.")
    team_name: str = Field(min_length=1, description="Name for the season.")
    season: int = Field(gt=0, description="Season compared.")
    team_runs_per_game: float = Field(
        ge=0,
        description="The selected team's average across its stored games, taken "
        "from TeamRunsSummary.season_average so the page cannot disagree with "
        "itself.",
    )
    league: LeagueRunsContext = Field(description="MLB-wide context compared against.")
    difference_vs_mlb: float = Field(
        description="team_runs_per_game - league.runs_per_game. Positive means "
        "the team scored more runs per game than MLB overall, negative fewer.",
    )

    @model_validator(mode="after")
    def _comparison_is_internally_consistent(self) -> TeamRunsLeagueComparison:
        if self.season != self.league.season:
            raise ValueError(
                f"season ({self.season}) must match the league context season "
                f"({self.league.season})"
            )
        expected = self.team_runs_per_game - self.league.runs_per_game
        if not isclose(self.difference_vs_mlb, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"difference_vs_mlb ({self.difference_vs_mlb}) must equal "
                f"team_runs_per_game - league.runs_per_game ({expected})"
            )
        return self


class TeamBaserunnersPoint(BaseModel):
    """One completed game plotted on the team baserunners chart."""

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
    baserunners: int = Field(
        ge=0,
        description="Times the team's hitters reached base by hit, walk, or "
        "hit-by-pitch in this game: hits + base_on_balls + hit_by_pitch. Never "
        "None: a game with an unknown component total is refused rather than "
        "plotted.",
    )
    rolling_average: float = Field(
        ge=0,
        description="Trailing rolling baserunners-per-game average ending at "
        "this game.",
    )


class TeamBaserunnersSummary(BaseModel):
    """Headline numbers describing a team-season's baserunners.

    ``season_average`` is the single authoritative season average; the chart's
    reference line and the summary card both read it from here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    games_played: int = Field(ge=1, description="Completed games analysed.")
    season_average: float = Field(
        ge=0,
        description="Baserunners per game across the stored completed games.",
    )
    recent_average: float = Field(
        ge=0,
        description="Baserunners per game over the most recent rolling window.",
    )
    prior_window_average: float | None = Field(
        default=None,
        ge=0,
        description="Baserunners per game over the window immediately before "
        "the recent one, or None when two complete windows do not exist.",
    )
    change_vs_prior_window: float | None = Field(
        default=None,
        description="recent_average - prior_window_average, or None.",
    )

    @model_validator(mode="after")
    def _prior_window_fields_agree(self) -> TeamBaserunnersSummary:
        has_prior = self.prior_window_average is not None
        has_change = self.change_vs_prior_window is not None
        if has_prior != has_change:
            raise ValueError(
                "prior_window_average and change_vs_prior_window must both be "
                "present or both be None"
            )
        return self


class TeamBaserunnersAnalysis(BaseModel):
    """A team-season's baserunners trend, ready to chart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Historical name for the season.")
    season: int = Field(gt=0, description="Season analysed.")
    rolling_window: int = Field(ge=1, description="Games in the trailing window.")
    points: tuple[TeamBaserunnersPoint, ...] = Field(
        min_length=1, description="Games in chart order."
    )
    summary: TeamBaserunnersSummary

    @model_validator(mode="after")
    def _summary_matches_points(self) -> TeamBaserunnersAnalysis:
        if self.summary.games_played != len(self.points):
            raise ValueError(
                "summary.games_played must equal the number of chart points"
            )
        return self

    @property
    def last_game_date(self) -> date:
        """Date of the most recent completed game in the analysis."""
        return self.points[-1].game_date


class LeagueBaserunnersContext(BaseModel):
    """MLB-wide baserunners context for one season.

    Built from every persisted team-game batting line for the season, so
    ``baserunners_per_game`` is a game-weighted mean across team-game records
    rather than the unweighted mean of each club's own average. Teams do not
    all play the same number of games, so those two numbers are not the same
    statistic.

    Every counted record must carry known ``hits``, ``base_on_balls``, and
    ``hit_by_pitch`` totals. A season holding even one record missing any of
    those three cannot produce this context at all, because an average over
    the rows that happen to have every value is not an MLB-wide average.

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
    total_baserunners: int = Field(
        ge=0,
        description="Baserunners (hits + base_on_balls + hit_by_pitch) summed "
        "across every counted team-game record.",
    )
    baserunners_per_game: float = Field(
        ge=0,
        description="total_baserunners / team_game_records.",
    )

    @model_validator(mode="after")
    def _baserunners_per_game_matches_the_totals(self) -> LeagueBaserunnersContext:
        expected = self.total_baserunners / self.team_game_records
        if not isclose(self.baserunners_per_game, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"baserunners_per_game ({self.baserunners_per_game}) must equal "
                f"total_baserunners / team_game_records ({expected})"
            )
        if self.teams_represented > self.team_game_records:
            raise ValueError(
                f"teams_represented ({self.teams_represented}) cannot exceed "
                f"team_game_records ({self.team_game_records})"
            )
        return self


class TeamBaserunnersLeagueComparison(BaseModel):
    """One team's baserunners per game placed beside MLB overall for the same season.

    Purely descriptive. A difference here says the selected team put runners on
    base more or fewer times per game than MLB across the stored season; it
    carries no claim of significance, and neither direction is labelled good or
    bad. Reaching base more often is not automatically better than a club doing
    other things well.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id of the selected team.")
    team_name: str = Field(min_length=1, description="Name for the season.")
    season: int = Field(gt=0, description="Season compared.")
    team_baserunners_per_game: float = Field(
        ge=0,
        description="The selected team's average across its stored games, taken "
        "from TeamBaserunnersSummary.season_average so the page cannot disagree "
        "with itself.",
    )
    league: LeagueBaserunnersContext = Field(
        description="MLB-wide context compared against."
    )
    difference_vs_mlb: float = Field(
        description="team_baserunners_per_game - league.baserunners_per_game. "
        "Positive means the team put runners on base more times per game than "
        "MLB overall, negative fewer.",
    )

    @model_validator(mode="after")
    def _comparison_is_internally_consistent(self) -> TeamBaserunnersLeagueComparison:
        if self.season != self.league.season:
            raise ValueError(
                f"season ({self.season}) must match the league context season "
                f"({self.league.season})"
            )
        expected = self.team_baserunners_per_game - self.league.baserunners_per_game
        if not isclose(self.difference_vs_mlb, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"difference_vs_mlb ({self.difference_vs_mlb}) must equal "
                f"team_baserunners_per_game - league.baserunners_per_game "
                f"({expected})"
            )
        return self


class TeamRunDifferentialPoint(BaseModel):
    """One completed game plotted on the team run differential chart.

    Unlike every other per-game point in this module, ``run_differential`` is
    signed: a team can be outscored, and the chart's zero line is the whole
    point of the page.
    """

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
    runs_scored: int = Field(ge=0, description="Runs scored by the team.")
    runs_allowed: int = Field(
        ge=0, description="Runs scored by the opponent in the same game."
    )
    run_differential: int = Field(
        description="runs_scored - runs_allowed. Negative when outscored."
    )
    is_win: bool = Field(description="Whether the team outscored the opponent.")
    rolling_average: float = Field(
        description="Trailing rolling run-differential average ending at this game. "
        "Signed, so this field has no lower bound.",
    )

    @model_validator(mode="after")
    def _differential_and_result_match_the_runs(self) -> TeamRunDifferentialPoint:
        expected = self.runs_scored - self.runs_allowed
        if self.run_differential != expected:
            raise ValueError(
                f"run_differential ({self.run_differential}) must equal "
                f"runs_scored - runs_allowed ({expected})"
            )
        if self.is_win != (self.runs_scored > self.runs_allowed):
            raise ValueError(
                f"is_win ({self.is_win}) must equal runs_scored > runs_allowed "
                f"({self.runs_scored} > {self.runs_allowed})"
            )
        return self


class PythagoreanRecord(BaseModel):
    """Expected record from runs scored and allowed, beside the actual record.

    Pythagorean expectation estimates the winning percentage a team's run
    scoring and run prevention *should* have produced, using the Bill James
    formula with the exponent 1.83 that Baseball Reference settled on::

        expected_win_pct = RS^1.83 / (RS^1.83 + RA^1.83)

    The gap between expected and actual is the interesting number. A team well
    above its expectation has usually won a lot of close games and lost a few
    blowouts, which historically does not persist; a team below it has usually
    done the reverse. It is a description of what has already happened, not a
    forecast, and one season is a small enough sample that a few games of gap
    is noise.

    The formula is undefined when a team has neither scored nor allowed a run,
    which cannot happen across any real completed game, so it is not modelled.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    exponent: float = Field(
        gt=0, description="Exponent used in the Pythagorean formula."
    )
    runs_scored: int = Field(ge=0, description="Runs scored across the season.")
    runs_allowed: int = Field(ge=0, description="Runs allowed across the season.")
    expected_win_pct: float = Field(
        ge=0, le=1, description="Pythagorean expected winning percentage."
    )
    expected_wins: float = Field(
        ge=0, description="expected_win_pct * games_played, not rounded."
    )
    actual_wins: int = Field(ge=0, description="Games the team outscored the opponent.")
    actual_losses: int = Field(ge=0, description="Games the team was outscored.")
    actual_win_pct: float = Field(
        ge=0, le=1, description="actual_wins / (actual_wins + actual_losses)."
    )
    wins_above_expectation: float = Field(
        description="actual_wins - expected_wins. Positive means the team has won "
        "more than its run scoring and prevention alone would predict.",
    )

    @model_validator(mode="after")
    def _record_is_internally_consistent(self) -> PythagoreanRecord:
        games = self.actual_wins + self.actual_losses
        if games == 0:
            raise ValueError("A Pythagorean record needs at least one decided game")

        expected_pct = self.runs_scored**self.exponent / (
            self.runs_scored**self.exponent + self.runs_allowed**self.exponent
        )
        if not isclose(self.expected_win_pct, expected_pct, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"expected_win_pct ({self.expected_win_pct}) must equal "
                f"RS^{self.exponent} / (RS^{self.exponent} + RA^{self.exponent}) "
                f"({expected_pct})"
            )
        if not isclose(
            self.expected_wins,
            self.expected_win_pct * games,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                f"expected_wins ({self.expected_wins}) must equal expected_win_pct "
                f"* games played ({self.expected_win_pct * games})"
            )
        if not isclose(
            self.actual_win_pct, self.actual_wins / games, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError(
                f"actual_win_pct ({self.actual_win_pct}) must equal actual_wins / "
                f"games played ({self.actual_wins / games})"
            )
        if not isclose(
            self.wins_above_expectation,
            self.actual_wins - self.expected_wins,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                f"wins_above_expectation ({self.wins_above_expectation}) must equal "
                f"actual_wins - expected_wins "
                f"({self.actual_wins - self.expected_wins})"
            )
        return self


class TeamRunDifferentialSummary(BaseModel):
    """Headline numbers describing a team-season's run differential.

    ``season_average`` is the single authoritative season average, read by the
    chart's reference line and the summary cards alike, so the page cannot show
    two different figures for the same statistic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    games_played: int = Field(ge=1, description="Completed games analysed.")
    total_runs_scored: int = Field(ge=0, description="Runs scored across the season.")
    total_runs_allowed: int = Field(ge=0, description="Runs allowed across the season.")
    total_run_differential: int = Field(
        description="total_runs_scored - total_runs_allowed. Signed."
    )
    season_average: float = Field(
        description="Run differential per game across the stored completed games. "
        "Signed, so this field has no lower bound.",
    )
    recent_average: float = Field(
        description="Run differential per game over the most recent rolling window."
    )
    prior_window_average: float | None = Field(
        default=None,
        description="Run differential per game over the window immediately before "
        "the recent one, or None when two complete windows do not exist.",
    )
    change_vs_prior_window: float | None = Field(
        default=None,
        description="recent_average - prior_window_average, or None.",
    )

    @model_validator(mode="after")
    def _totals_and_windows_agree(self) -> TeamRunDifferentialSummary:
        expected_total = self.total_runs_scored - self.total_runs_allowed
        if self.total_run_differential != expected_total:
            raise ValueError(
                f"total_run_differential ({self.total_run_differential}) must equal "
                f"total_runs_scored - total_runs_allowed ({expected_total})"
            )
        expected_average = self.total_run_differential / self.games_played
        if not isclose(
            self.season_average, expected_average, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError(
                f"season_average ({self.season_average}) must equal "
                f"total_run_differential / games_played ({expected_average})"
            )
        has_prior = self.prior_window_average is not None
        has_change = self.change_vs_prior_window is not None
        if has_prior != has_change:
            raise ValueError(
                "prior_window_average and change_vs_prior_window must both be "
                "present or both be None"
            )
        return self


class TeamRunDifferentialAnalysis(BaseModel):
    """A team-season's run differential trend and Pythagorean record, ready to chart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Historical name for the season.")
    season: int = Field(gt=0, description="Season analysed.")
    rolling_window: int = Field(ge=1, description="Games in the trailing window.")
    points: tuple[TeamRunDifferentialPoint, ...] = Field(
        min_length=1, description="Games in chart order."
    )
    summary: TeamRunDifferentialSummary
    pythagorean: PythagoreanRecord

    @model_validator(mode="after")
    def _summary_matches_points(self) -> TeamRunDifferentialAnalysis:
        if self.summary.games_played != len(self.points):
            raise ValueError(
                "summary.games_played must equal the number of chart points"
            )
        decided = self.pythagorean.actual_wins + self.pythagorean.actual_losses
        if decided != len(self.points):
            raise ValueError(
                f"pythagorean wins plus losses ({decided}) must equal the number "
                f"of chart points ({len(self.points)})"
            )
        if self.pythagorean.runs_scored != self.summary.total_runs_scored:
            raise ValueError(
                f"pythagorean.runs_scored ({self.pythagorean.runs_scored}) must "
                f"equal summary.total_runs_scored ({self.summary.total_runs_scored})"
            )
        if self.pythagorean.runs_allowed != self.summary.total_runs_allowed:
            raise ValueError(
                f"pythagorean.runs_allowed ({self.pythagorean.runs_allowed}) must "
                f"equal summary.total_runs_allowed ({self.summary.total_runs_allowed})"
            )
        return self

    @property
    def last_game_date(self) -> date:
        """Date of the most recent completed game in the analysis."""
        return self.points[-1].game_date


class TeamPitchingRates(BaseModel):
    """Pitching rates over a set of games, with the totals they came from.

    Every rate is carried beside its own numerator and denominator so the
    validators below can prove the figures agree. A rate that has drifted from
    its components is not constructible.

    Innings appear only as ``innings_pitched``, a true fraction derived from
    ``outs``. Baseball notation — where ``10.2`` means ten and two-thirds — is
    a display concern and never a number in this model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outs: int = Field(gt=0, description="Outs recorded across the games in scope.")
    innings_pitched: float = Field(
        gt=0, description="outs / 3, as a true fraction rather than baseball notation."
    )
    earned_runs: int = Field(ge=0, description="Earned runs allowed.")
    hits_allowed: int = Field(ge=0, description="Hits allowed.")
    base_on_balls: int = Field(ge=0, description="Walks issued.")
    strikeouts: int = Field(ge=0, description="Strikeouts recorded.")
    home_runs_allowed: int = Field(ge=0, description="Home runs allowed.")
    number_of_pitches: int = Field(ge=0, description="Pitches thrown.")
    strikes: int = Field(
        ge=0, description="Pitches that were strikes. Never exceeds the total."
    )
    pitches_per_game: float = Field(
        ge=0,
        description="number_of_pitches / games. A count per game, so unlike the "
        "rates below it is a plain mean and aggregates by averaging.",
    )
    strike_percentage: float = Field(
        ge=0, le=1, description="strikes / number_of_pitches."
    )
    era: float = Field(
        ge=0, description="Earned runs per nine innings: earned_runs * 27 / outs."
    )
    whip: float = Field(
        ge=0,
        description="Walks and hits per inning pitched: "
        "(hits_allowed + base_on_balls) / innings_pitched.",
    )
    strikeouts_per_nine: float = Field(ge=0, description="strikeouts * 27 / outs.")
    walks_per_nine: float = Field(ge=0, description="base_on_balls * 27 / outs.")

    @model_validator(mode="after")
    def _rates_match_their_components(self) -> TeamPitchingRates:
        expected_innings = self.outs / 3
        if not isclose(
            self.innings_pitched, expected_innings, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError(
                f"innings_pitched ({self.innings_pitched}) must equal outs / 3 "
                f"({expected_innings})"
            )
        for name, numerator in (
            ("era", self.earned_runs),
            ("strikeouts_per_nine", self.strikeouts),
            ("walks_per_nine", self.base_on_balls),
        ):
            expected = numerator * 27 / self.outs
            if not isclose(getattr(self, name), expected, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(
                    f"{name} ({getattr(self, name)}) must equal its numerator "
                    f"* 27 / outs ({expected})"
                )
        expected_whip = (self.hits_allowed + self.base_on_balls) / self.innings_pitched
        if not isclose(self.whip, expected_whip, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"whip ({self.whip}) must equal (hits_allowed + base_on_balls) "
                f"/ innings_pitched ({expected_whip})"
            )
        if self.home_runs_allowed > self.hits_allowed:
            raise ValueError(
                f"home_runs_allowed ({self.home_runs_allowed}) cannot exceed "
                f"hits_allowed ({self.hits_allowed})"
            )
        if self.strikes > self.number_of_pitches:
            raise ValueError(
                f"strikes ({self.strikes}) cannot exceed number_of_pitches "
                f"({self.number_of_pitches})"
            )
        if self.number_of_pitches:
            expected_strike_pct = self.strikes / self.number_of_pitches
            if not isclose(
                self.strike_percentage, expected_strike_pct, rel_tol=1e-9, abs_tol=1e-9
            ):
                raise ValueError(
                    f"strike_percentage ({self.strike_percentage}) must equal "
                    f"strikes / number_of_pitches ({expected_strike_pct})"
                )
        return self


class TeamPitchingPoint(BaseModel):
    """One completed game plotted on the team pitching chart.

    ``game_era`` is that single game's earned runs per nine innings, which is
    the raw series. ``rolling_era`` is the trailing-window ERA, aggregated by
    summing earned runs and outs across the window rather than by averaging
    game ERAs — the two are different numbers.
    """

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
    outs: int = Field(gt=0, description="Outs recorded in this game.")
    innings_pitched_display: str = Field(
        min_length=1,
        description="Innings in the baseball notation a box score prints, such "
        "as '10.2' for 32 outs. Display only; never used in a calculation.",
    )
    earned_runs: int = Field(ge=0, description="Earned runs allowed in this game.")
    runs_allowed: int = Field(ge=0, description="Runs allowed, earned or not.")
    hits_allowed: int = Field(ge=0, description="Hits allowed in this game.")
    base_on_balls: int = Field(ge=0, description="Walks issued in this game.")
    strikeouts: int = Field(ge=0, description="Strikeouts recorded in this game.")
    number_of_pitches: int = Field(ge=0, description="Pitches thrown in this game.")
    strikes: int = Field(ge=0, description="Pitches that were strikes.")
    game_era: float = Field(
        ge=0, description="This game's earned runs per nine innings."
    )
    rolling_era: float = Field(
        ge=0, description="Trailing rolling ERA ending at this game."
    )

    @model_validator(mode="after")
    def _game_era_matches_the_components(self) -> TeamPitchingPoint:
        expected = self.earned_runs * 27 / self.outs
        if not isclose(self.game_era, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"game_era ({self.game_era}) must equal earned_runs * 27 / outs "
                f"({expected})"
            )
        if self.earned_runs > self.runs_allowed:
            raise ValueError(
                f"earned_runs ({self.earned_runs}) cannot exceed runs_allowed "
                f"({self.runs_allowed})"
            )
        return self


class TeamPitchingSummary(BaseModel):
    """Headline numbers describing a team-season's pitching.

    ``season`` holds the single authoritative set of season rates; the chart's
    reference line, the summary cards, and the MLB comparison all read from it,
    so the page cannot show two different ERAs for the same team.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    games_played: int = Field(ge=1, description="Completed games analysed.")
    season: TeamPitchingRates
    recent_era: float = Field(
        ge=0, description="ERA over the most recent rolling window."
    )
    prior_window_era: float | None = Field(
        default=None,
        ge=0,
        description="ERA over the window immediately before the recent one, or "
        "None when two complete windows do not exist.",
    )
    change_vs_prior_window: float | None = Field(
        default=None,
        description="recent_era - prior_window_era, or None. Negative is an "
        "improvement, since a lower ERA is better.",
    )

    @model_validator(mode="after")
    def _prior_window_fields_agree(self) -> TeamPitchingSummary:
        has_prior = self.prior_window_era is not None
        has_change = self.change_vs_prior_window is not None
        if has_prior != has_change:
            raise ValueError(
                "prior_window_era and change_vs_prior_window must both be "
                "present or both be None"
            )
        return self


class TeamPitchingAnalysis(BaseModel):
    """A team-season's pitching trend, ready to chart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Historical name for the season.")
    season: int = Field(gt=0, description="Season analysed.")
    rolling_window: int = Field(ge=1, description="Games in the trailing window.")
    points: tuple[TeamPitchingPoint, ...] = Field(
        min_length=1, description="Games in chart order."
    )
    summary: TeamPitchingSummary

    @model_validator(mode="after")
    def _summary_matches_points(self) -> TeamPitchingAnalysis:
        if self.summary.games_played != len(self.points):
            raise ValueError(
                "summary.games_played must equal the number of chart points"
            )
        charted_outs = sum(point.outs for point in self.points)
        if charted_outs != self.summary.season.outs:
            raise ValueError(
                f"summary.season.outs ({self.summary.season.outs}) must equal the "
                f"outs across the chart points ({charted_outs})"
            )
        charted_earned_runs = sum(point.earned_runs for point in self.points)
        if charted_earned_runs != self.summary.season.earned_runs:
            raise ValueError(
                f"summary.season.earned_runs ({self.summary.season.earned_runs}) "
                f"must equal the earned runs across the chart points "
                f"({charted_earned_runs})"
            )
        return self

    @property
    def last_game_date(self) -> date:
        """Date of the most recent completed game in the analysis."""
        return self.points[-1].game_date


class LeaguePitchingContext(BaseModel):
    """MLB-wide pitching context for one season.

    Built from every persisted team-game pitching line for the season. The
    rates are **outs-weighted**, not game-weighted: ERA and WHIP are per-inning
    rates, so the denominator is the innings actually pitched rather than a
    count of team-game records. That is a different weighting from the other
    league contexts in this module, and it is the one a published league ERA
    uses.

    Every field describes the games **currently stored** for the season. For a
    season still being played that is the completed games held by the most
    recent complete league-wide refresh, not a whole season.

    A league season imported before pitching was collected has no rows here at
    all, which is a different state from a season with poor pitching.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    season: int = Field(gt=0, description="Season the context describes.")
    teams_represented: int = Field(
        ge=1,
        description="Distinct teams with at least one stored pitching line.",
    )
    team_game_records: int = Field(
        ge=1,
        description="Team-game pitching lines counted. One MLB game contributes "
        "two records once both clubs are stored, so this is not a game count.",
    )
    outs: int = Field(gt=0, description="Outs recorded across every counted record.")
    innings_pitched: float = Field(gt=0, description="outs / 3.")
    total_earned_runs: int = Field(ge=0, description="Earned runs allowed in total.")
    era: float = Field(ge=0, description="total_earned_runs * 27 / outs.")
    whip: float = Field(ge=0, description="Walks and hits per inning pitched.")
    strikeouts_per_nine: float = Field(ge=0, description="Strikeouts per nine innings.")
    walks_per_nine: float = Field(ge=0, description="Walks per nine innings.")

    @model_validator(mode="after")
    def _rates_match_the_totals(self) -> LeaguePitchingContext:
        expected_innings = self.outs / 3
        if not isclose(
            self.innings_pitched, expected_innings, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError(
                f"innings_pitched ({self.innings_pitched}) must equal outs / 3 "
                f"({expected_innings})"
            )
        expected_era = self.total_earned_runs * 27 / self.outs
        if not isclose(self.era, expected_era, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"era ({self.era}) must equal total_earned_runs * 27 / outs "
                f"({expected_era})"
            )
        if self.teams_represented > self.team_game_records:
            raise ValueError(
                f"teams_represented ({self.teams_represented}) cannot exceed "
                f"team_game_records ({self.team_game_records})"
            )
        return self


class TeamPitchingLeagueComparison(BaseModel):
    """One team's pitching rates placed beside MLB overall for the same season.

    Note the sign convention, which is the opposite of every other comparison
    in this module: a **negative** difference is the better direction, because
    a lower ERA and a lower WHIP are better. The templates and formatters are
    responsible for saying so rather than leaving a reader to assume that
    positive means good.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Display name for the season.")
    season: int = Field(gt=0, description="Season compared.")
    team_era: float = Field(ge=0, description="The team's season ERA.")
    team_whip: float = Field(ge=0, description="The team's season WHIP.")
    team_strikeouts_per_nine: float = Field(ge=0, description="The team's K/9.")
    team_walks_per_nine: float = Field(ge=0, description="The team's BB/9.")
    league: LeaguePitchingContext
    era_difference_vs_mlb: float = Field(
        description="team_era - league.era. Negative means the team allowed "
        "fewer earned runs per nine innings than MLB, which is better.",
    )
    whip_difference_vs_mlb: float = Field(
        description="team_whip - league.whip. Negative is better.",
    )

    @model_validator(mode="after")
    def _comparison_is_internally_consistent(self) -> TeamPitchingLeagueComparison:
        if self.season != self.league.season:
            raise ValueError(
                f"season ({self.season}) must match the league context season "
                f"({self.league.season})"
            )
        for name, team_value, league_value in (
            ("era_difference_vs_mlb", self.team_era, self.league.era),
            ("whip_difference_vs_mlb", self.team_whip, self.league.whip),
        ):
            expected = team_value - league_value
            if not isclose(getattr(self, name), expected, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(
                    f"{name} ({getattr(self, name)}) must equal the team value "
                    f"minus the league value ({expected})"
                )
        return self
