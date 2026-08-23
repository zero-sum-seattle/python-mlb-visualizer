"""MLB-wide pitching calculations over normalized game pitching lines.

Answers one question:

    How does this team's run prevention compare with MLB overall?

Separate from ``app/analytics/team_pitching.py`` because it describes MLB
rather than one club, and separate from the other league modules because
pitching is a different stat group living in a different table. Like every
other module under ``app/analytics``, this one knows nothing about FastAPI,
Jinja, SQLAlchemy, Plotly, or the MLB API.

The league rates here are **outs-weighted**, not game-weighted. Every other
league context in this package divides a total by a count of team-game records,
because those metrics are per-game counts. ERA and WHIP are not: they are
per-inning rates, so the denominator is the innings actually pitched. A club
that has played more games — or more extra innings — contributes proportionally
more, which is what a published league ERA does.

One consequence worth stating: league ERA is not zero-sum the way run
differential is. Earned runs allowed league-wide are a real total, so unlike
``/run-differential`` this page has a genuine MLB average to draw.
"""

from collections.abc import Sequence

from app.analytics.league_hitting import supports_league_wide_average
from app.schemas.analytics import (
    LeaguePitchingContext,
    TeamPitchingAnalysis,
    TeamPitchingLeagueComparison,
)
from app.schemas.games import (
    OUTS_PER_INNING,
    OUTS_PER_NINE_INNINGS,
    TeamGamePitchingLine,
)
from app.schemas.ingestion import LeagueSeasonIngestionState


class LeaguePitchingAnalysisError(ValueError):
    """League pitching analysis was requested with input it cannot describe."""


def supports_league_wide_pitching_average(
    coverage: LeagueSeasonIngestionState | None,
) -> bool:
    """Say whether a season's coverage permits an MLB-wide pitching average.

    This is the same Milestone 5 coverage rule the other league pages use,
    deliberately delegated rather than re-implemented so the copies cannot
    drift and let one page call a season MLB-wide while another does not.

    Complete coverage is necessary but **not** sufficient here, and the caller
    must check the second condition itself: a league season imported before
    pitching was collected has complete batting coverage and no pitching rows
    at all. ``build_league_pitching_context`` refuses an empty set of records,
    which is what that state produces.
    """
    return supports_league_wide_average(coverage)


def build_league_pitching_context(
    games: Sequence[TeamGamePitchingLine],
) -> LeaguePitchingContext:
    """Calculate MLB pitching rates across every stored team-game pitching line.

    Every rate is outs-weighted::

        MLB ERA  = total earned runs * 27 / total outs
        MLB WHIP = (total hits + total walks) / (total outs / 3)

    Summing the numerators and denominators once is the same rule the team-side
    module follows, and for the same reason: averaging each club's own ERA
    would weight a club with 40 games like one with 162 and produce a figure
    matching no published source.

    Raises
    ------
    LeaguePitchingAnalysisError
        ``games`` is empty, the records span more than one season, or no outs
        were recorded across them.
    """
    if not games:
        raise LeaguePitchingAnalysisError(
            "Cannot describe MLB pitching from no team-game pitching records"
        )

    seasons = {game.season for game in games}
    if len(seasons) > 1:
        raise LeaguePitchingAnalysisError(
            f"All team-game records must belong to one season, got {sorted(seasons)}"
        )

    outs = sum(game.outs for game in games)
    if outs == 0:
        raise LeaguePitchingAnalysisError(
            "Cannot describe MLB pitching from records with no recorded outs"
        )

    earned_runs = sum(game.earned_runs for game in games)
    hits_allowed = sum(game.hits_allowed for game in games)
    base_on_balls = sum(game.base_on_balls for game in games)
    strikeouts = sum(game.strikeouts for game in games)
    innings = outs / OUTS_PER_INNING

    return LeaguePitchingContext(
        season=games[0].season,
        teams_represented=len({game.team_id for game in games}),
        team_game_records=len(games),
        outs=outs,
        innings_pitched=innings,
        total_earned_runs=earned_runs,
        era=earned_runs * OUTS_PER_NINE_INNINGS / outs,
        whip=(hits_allowed + base_on_balls) / innings,
        strikeouts_per_nine=strikeouts * OUTS_PER_NINE_INNINGS / outs,
        walks_per_nine=base_on_balls * OUTS_PER_NINE_INNINGS / outs,
    )


def compare_team_pitching_to_league(
    analysis: TeamPitchingAnalysis,
    league: LeaguePitchingContext,
) -> TeamPitchingLeagueComparison:
    """Place a team-season's pitching rates beside MLB overall.

    The team side reads ``TeamPitchingAnalysis.summary.season``, the same rates
    the chart's team reference line and the summary cards read, so the page
    cannot show two different ERAs for the same club.

    The differences are descriptive subtraction and nothing more: not
    normalized, not ranked, not tested for significance, and not adjusted for
    park or opponent. Note the sign convention — a **negative** ERA difference
    means the team allowed fewer earned runs per nine innings than MLB, which
    is the better direction. That is the opposite of every other page in this
    application, where a positive difference means more of the thing.

    Raises
    ------
    LeaguePitchingAnalysisError
        The team analysis and the league context describe different seasons.
    """
    if analysis.season != league.season:
        raise LeaguePitchingAnalysisError(
            f"Cannot compare a {analysis.season} team-season against "
            f"{league.season} MLB context"
        )

    team = analysis.summary.season
    return TeamPitchingLeagueComparison(
        team_id=analysis.team_id,
        team_name=analysis.team_name,
        season=analysis.season,
        team_era=team.era,
        team_whip=team.whip,
        team_strikeouts_per_nine=team.strikeouts_per_nine,
        team_walks_per_nine=team.walks_per_nine,
        league=league,
        era_difference_vs_mlb=team.era - league.era,
        whip_difference_vs_mlb=team.whip - league.whip,
    )
