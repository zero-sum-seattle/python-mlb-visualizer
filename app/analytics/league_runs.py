"""MLB-wide run-scoring calculations over normalized game batting lines.

Answers one question:

    How many runs per game does this team score compared with MLB overall?

Separate from ``app/analytics/team_runs.py`` because it describes MLB rather
than one club, and separate from ``app/analytics/league_hitting.py`` and
``app/analytics/league_strikeouts.py`` because runs are a third statistic with
their own labels and their own data history. Three explicit modules are easier
to read and change than one league-metric framework covering all of them. Like
every other module under ``app/analytics``, this one knows nothing about
FastAPI, Jinja, SQLAlchemy, Plotly, or the MLB API.

Runs scored throughout: runs the counted clubs put on the board. Because every
real game contributes one record per club, the same runs appear once as the
scoring team's total and never as the opponent's, so no run differential is
implied anywhere here.
"""

from collections.abc import Sequence

from app.analytics.league_hitting import supports_league_wide_average
from app.schemas.analytics import (
    LeagueRunsContext,
    TeamRunsAnalysis,
    TeamRunsLeagueComparison,
)
from app.schemas.games import TeamGameBattingLine
from app.schemas.ingestion import LeagueSeasonIngestionState


class LeagueRunsAnalysisError(ValueError):
    """League run analysis was requested with input it cannot describe."""


def supports_league_wide_runs_average(
    coverage: LeagueSeasonIngestionState | None,
) -> bool:
    """Say whether a season's coverage permits an MLB-wide runs average.

    This is the Milestone 5 coverage rule, unchanged and deliberately not
    re-implemented here: ``COMPLETE`` coverage from the latest league-wide
    refresh, never a row count, a team count, or a game count. Three copies of
    that rule could drift and let one page call a season MLB-wide while another
    did not.

    ``COMPLETE`` describes the refresh, not the season. An in-progress season
    whose latest league-wide run covered every discovered team qualifies, and
    what the resulting average describes is the completed games currently
    stored.

    Unlike batting strikeouts, complete coverage is both necessary **and**
    sufficient here: ``runs`` is required on every persisted team-game record,
    so a covered season cannot be holding unknown run totals.
    """
    return supports_league_wide_average(coverage)


def build_league_runs_context(
    games: Sequence[TeamGameBattingLine],
) -> LeagueRunsContext:
    """Calculate MLB runs per game across every stored team-game record.

    The average is **game-weighted**::

        MLB Runs/Game = total runs / total team-game records

    Every stored team-game record counts once, so a club that has played more
    games contributes proportionally more. Averaging each club's own average
    instead would silently give a team with 40 games the same weight as a team
    with 162, which answers a different question and is wrong for this one.

    The denominator counts team-game records, not MLB games: one real game
    produces two records once both clubs are stored. That makes this a per-team
    per-game number, which is what the team side of the comparison is too.

    Raises
    ------
    LeagueRunsAnalysisError
        ``games`` is empty, or the records span more than one season.
    """
    if not games:
        raise LeagueRunsAnalysisError(
            "Cannot describe MLB run scoring from no team-game records"
        )

    seasons = {game.season for game in games}
    if len(seasons) > 1:
        raise LeagueRunsAnalysisError(
            f"All team-game records must belong to one season, got {sorted(seasons)}"
        )

    team_game_records = len(games)
    total_runs = sum(game.runs for game in games)
    return LeagueRunsContext(
        season=games[0].season,
        teams_represented=len({game.team_id for game in games}),
        team_game_records=team_game_records,
        total_runs=total_runs,
        runs_per_game=total_runs / team_game_records,
    )


def compare_team_runs_to_league(
    analysis: TeamRunsAnalysis,
    league: LeagueRunsContext,
) -> TeamRunsLeagueComparison:
    """Place a team-season's runs per game beside MLB overall.

    The team side is ``TeamRunsAnalysis.summary.season_average``, which is the
    same number the chart's team reference line and the Season Avg card read,
    so the page cannot show two different team averages.

    The difference is descriptive subtraction and nothing more. It is not
    normalized, not ranked, not tested for significance, not adjusted for park
    or opponent, and it says nothing about the runs the team allowed.

    Raises
    ------
    LeagueRunsAnalysisError
        The team analysis and the league context describe different seasons.
    """
    if analysis.season != league.season:
        raise LeagueRunsAnalysisError(
            f"Cannot compare a {analysis.season} team-season against "
            f"{league.season} MLB context"
        )

    team_runs_per_game = analysis.summary.season_average
    return TeamRunsLeagueComparison(
        team_id=analysis.team_id,
        team_name=analysis.team_name,
        season=analysis.season,
        team_runs_per_game=team_runs_per_game,
        league=league,
        difference_vs_mlb=team_runs_per_game - league.runs_per_game,
    )
