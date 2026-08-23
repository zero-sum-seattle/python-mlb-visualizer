"""MLB-wide baserunners calculations over normalized game batting lines.

Answers one question:

    How many times per game does this team put a runner on base compared with
    MLB overall?

Separate from ``app/analytics/team_baserunners.py`` because it describes MLB
rather than one club, and separate from the other league modules because
baserunners has its own data history: it needs two components (walks and
hit-by-pitch) that were not persisted until this metric shipped, not one like
batting strikeouts. Like every other module under ``app/analytics``, it knows
nothing about FastAPI, Jinja, SQLAlchemy, Plotly, or the MLB API.

Baserunners throughout: hits + walks + hit-by-pitch, the standard OBP
numerator excluding reached-on-error and fielder's choice. Baserunners/Game is
a per-game count, not OBP, and neither direction is treated as good or bad.
"""

from collections.abc import Sequence

from app.analytics.league_hitting import supports_league_wide_average
from app.schemas.analytics import (
    LeagueBaserunnersContext,
    TeamBaserunnersAnalysis,
    TeamBaserunnersLeagueComparison,
)
from app.schemas.games import TeamGameBattingLine
from app.schemas.ingestion import LeagueSeasonIngestionState


class LeagueBaserunnersAnalysisError(ValueError):
    """League baserunners analysis was requested with input it cannot describe."""


class MissingLeagueBaserunnerDataError(LeagueBaserunnersAnalysisError):
    """Some stored league records have no walk or hit-by-pitch total.

    Raised rather than worked around. Rows imported before these two columns
    were persisted hold ``NULL``, which means unknown, not zero. Averaging the
    rows that happen to carry both values and calling the result MLB-wide would
    describe a subset of the league while claiming to describe all of it, so
    the season yields no MLB average until it is backfilled.

    Note that a season can hold complete league *coverage* and still land here:
    coverage records that every discovered team was refreshed, which says
    nothing about whether older rows were rewritten with these two totals.
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
            f"{season} have no walk or hit-by-pitch total. Those records were "
            f"imported before baserunner components were persisted; re-import "
            f"the league season to backfill them."
        )


def supports_league_wide_baserunners_average(
    coverage: LeagueSeasonIngestionState | None,
) -> bool:
    """Say whether a season's coverage permits an MLB-wide baserunners average.

    This is the Milestone 5 coverage rule, unchanged and deliberately not
    re-implemented here: ``COMPLETE`` coverage from the latest league-wide
    refresh, never a row count, a team count, or a game count. Two copies of
    that rule could drift and let one page call a season MLB-wide while the
    other did not.

    Complete coverage is necessary but **not** sufficient for baserunners.
    Every counted record must also carry known walk and hit-by-pitch totals,
    which only the stored records themselves can answer;
    ``build_league_baserunners_context`` enforces that half.
    """
    return supports_league_wide_average(coverage)


def build_league_baserunners_context(
    games: Sequence[TeamGameBattingLine],
) -> LeagueBaserunnersContext:
    """Calculate MLB baserunners per game across stored team-game records.

    The average is **game-weighted**::

        Baserunners/Game = total baserunners / total team-game records

    Every stored team-game record counts once, so a club that has played more
    games contributes proportionally more. Averaging each club's own average
    instead would silently give a team with 40 games the same weight as a team
    with 162, which answers a different question and is wrong for this one.

    Raises
    ------
    MissingLeagueBaserunnerDataError
        At least one record has ``base_on_balls`` or ``hit_by_pitch`` of None.
        Records are neither dropped nor read as zero.
    LeagueBaserunnersAnalysisError
        ``games`` is empty, or the records span more than one season.
    """
    if not games:
        raise LeagueBaserunnersAnalysisError(
            "Cannot describe MLB baserunners from no team-game records"
        )

    seasons = {game.season for game in games}
    if len(seasons) > 1:
        raise LeagueBaserunnersAnalysisError(
            f"All team-game records must belong to one season, got {sorted(seasons)}"
        )

    season = games[0].season
    missing = [
        game
        for game in games
        if game.base_on_balls is None or game.hit_by_pitch is None
    ]
    if missing:
        raise MissingLeagueBaserunnerDataError(
            season=season,
            records_missing=len(missing),
            records_total=len(games),
        )

    # Every component is known past this point, which the None check above
    # proves.
    team_game_records = len(games)
    total_baserunners = sum(
        game.hits + game.base_on_balls + game.hit_by_pitch
        for game in games
        if game.base_on_balls is not None and game.hit_by_pitch is not None
    )
    return LeagueBaserunnersContext(
        season=season,
        teams_represented=len({game.team_id for game in games}),
        team_game_records=team_game_records,
        total_baserunners=total_baserunners,
        baserunners_per_game=total_baserunners / team_game_records,
    )


def compare_team_baserunners_to_league(
    analysis: TeamBaserunnersAnalysis,
    league: LeagueBaserunnersContext,
) -> TeamBaserunnersLeagueComparison:
    """Place a team-season's Baserunners/Game beside MLB overall.

    The team side is ``TeamBaserunnersAnalysis.summary.season_average``, which
    is the same number the chart's team reference line and the Season Avg card
    read, so the page cannot show two different team averages.

    The difference is descriptive subtraction and nothing more. It is not
    normalized, not ranked, not tested for significance, and neither direction
    is favourable by definition.

    Raises
    ------
    LeagueBaserunnersAnalysisError
        The team analysis and the league context describe different seasons.
    """
    if analysis.season != league.season:
        raise LeagueBaserunnersAnalysisError(
            f"Cannot compare a {analysis.season} team-season against "
            f"{league.season} MLB context"
        )

    team_baserunners_per_game = analysis.summary.season_average
    return TeamBaserunnersLeagueComparison(
        team_id=analysis.team_id,
        team_name=analysis.team_name,
        season=analysis.season,
        team_baserunners_per_game=team_baserunners_per_game,
        league=league,
        difference_vs_mlb=team_baserunners_per_game - league.baserunners_per_game,
    )
