"""MLB-wide batting strikeout calculations over normalized game batting lines.

Answers one question:

    How many times per game does this team's offense strike out compared with
    MLB overall?

Separate from ``app/analytics/team_strikeouts.py`` because it describes MLB
rather than one club, and separate from ``app/analytics/league_hitting.py``
because hits and batting strikeouts are different statistics with different
data histories: hits have always been persisted, batting strikeouts have not.
That difference is the whole reason this module exists rather than a shared
league-metric abstraction. Like every other module under ``app/analytics``, it
knows nothing about FastAPI, Jinja, SQLAlchemy, Plotly, or the MLB API.

Batting strikeouts throughout: times the team's own hitters struck out, never
strikeouts recorded by its pitchers. K/Game is a per-game count, not K%, and
neither direction is treated as good or bad.
"""

from collections.abc import Sequence

from app.analytics.league_hitting import supports_league_wide_average
from app.schemas.analytics import (
    LeagueStrikeoutsContext,
    TeamStrikeoutsAnalysis,
    TeamStrikeoutsLeagueComparison,
)
from app.schemas.games import TeamGameBattingLine
from app.schemas.ingestion import LeagueSeasonIngestionState


class LeagueStrikeoutsAnalysisError(ValueError):
    """League strikeout analysis was requested with input it cannot describe."""


class MissingLeagueStrikeoutDataError(LeagueStrikeoutsAnalysisError):
    """Some stored league records have no batting strikeout total.

    Raised rather than worked around. Rows imported before batting strikeouts
    were persisted hold ``NULL``, which means unknown, not zero. Averaging the
    rows that happen to carry a value and calling the result MLB-wide would
    describe a subset of the league while claiming to describe all of it, so
    the season yields no MLB average until it is backfilled.

    Note that a season can hold complete league *coverage* and still land here:
    coverage records that every discovered team was refreshed, which says
    nothing about whether older rows were rewritten with strikeout totals.
    """

    def __init__(
        self,
        *,
        season: int,
        records_missing: int,
        records_total: int,
    ) -> None:
        self.season = season
        self.records_missing = records_missing
        self.records_total = records_total
        super().__init__(
            f"{records_missing} of {records_total} stored team-game records for "
            f"{season} have no batting strikeout total. Those records were "
            f"imported before batting strikeouts were persisted; re-import the "
            f"league season to backfill them."
        )


def supports_league_wide_strikeout_average(
    coverage: LeagueSeasonIngestionState | None,
) -> bool:
    """Say whether a season's coverage permits an MLB-wide strikeout average.

    This is the Milestone 5 coverage rule, unchanged and deliberately not
    re-implemented here: ``COMPLETE`` coverage from the latest league-wide
    refresh, never a row count, a team count, or a game count. Two copies of
    that rule could drift and let one page call a season MLB-wide while the
    other did not.

    ``COMPLETE`` describes the refresh, not the season. An in-progress season
    whose latest league-wide run covered every discovered team qualifies, and
    what the resulting average describes is the completed games currently
    stored.

    Complete coverage is necessary but **not** sufficient for batting
    strikeouts. Every counted record must also carry a known strikeout total,
    which only the stored records themselves can answer;
    ``build_league_strikeouts_context`` enforces that half.
    """
    return supports_league_wide_average(coverage)


def build_league_strikeouts_context(
    games: Sequence[TeamGameBattingLine],
) -> LeagueStrikeoutsContext:
    """Calculate MLB batting strikeouts per game across stored team-game records.

    The average is **game-weighted**::

        batting K/Game = total batting strikeouts / total team-game records

    Every stored team-game record counts once, so a club that has played more
    games contributes proportionally more. Averaging each club's own average
    instead would silently give a team with 40 games the same weight as a team
    with 162, which answers a different question and is wrong for this one.

    Raises
    ------
    MissingLeagueStrikeoutDataError
        At least one record has ``strikeouts`` of None. Records are neither
        dropped nor read as zero.
    LeagueStrikeoutsAnalysisError
        ``games`` is empty, or the records span more than one season.
    """
    if not games:
        raise LeagueStrikeoutsAnalysisError(
            "Cannot describe MLB batting strikeouts from no team-game records"
        )

    seasons = {game.season for game in games}
    if len(seasons) > 1:
        raise LeagueStrikeoutsAnalysisError(
            f"All team-game records must belong to one season, got {sorted(seasons)}"
        )

    season = games[0].season
    missing = [game for game in games if game.strikeouts is None]
    if missing:
        raise MissingLeagueStrikeoutDataError(
            season=season,
            records_missing=len(missing),
            records_total=len(games),
        )

    # Every value is known past this point, which the None check above proves.
    team_game_records = len(games)
    total_strikeouts = sum(
        game.strikeouts for game in games if game.strikeouts is not None
    )
    return LeagueStrikeoutsContext(
        season=season,
        teams_represented=len({game.team_id for game in games}),
        team_game_records=team_game_records,
        total_strikeouts=total_strikeouts,
        strikeouts_per_game=total_strikeouts / team_game_records,
    )


def compare_team_strikeouts_to_league(
    analysis: TeamStrikeoutsAnalysis,
    league: LeagueStrikeoutsContext,
) -> TeamStrikeoutsLeagueComparison:
    """Place a team-season's batting K/Game beside MLB overall.

    The team side is ``TeamStrikeoutsAnalysis.summary.season_average``, which is
    the same number the chart's team reference line and the Season Avg card
    read, so the page cannot show two different team averages.

    The difference is descriptive subtraction and nothing more. It is not
    normalized, not ranked, not tested for significance, and neither direction
    is favourable by definition: fewer batting strikeouts per game is not
    automatically better hitting.

    Raises
    ------
    LeagueStrikeoutsAnalysisError
        The team analysis and the league context describe different seasons.
    """
    if analysis.season != league.season:
        raise LeagueStrikeoutsAnalysisError(
            f"Cannot compare a {analysis.season} team-season against "
            f"{league.season} MLB context"
        )

    team_strikeouts_per_game = analysis.summary.season_average
    return TeamStrikeoutsLeagueComparison(
        team_id=analysis.team_id,
        team_name=analysis.team_name,
        season=analysis.season,
        team_strikeouts_per_game=team_strikeouts_per_game,
        league=league,
        difference_vs_mlb=team_strikeouts_per_game - league.strikeouts_per_game,
    )
