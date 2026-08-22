"""Presentation helpers shared by the chart builder and the templates."""

from dataclasses import dataclass
from datetime import date

from app.schemas.analytics import (
    TeamBaserunnersAnalysis,
    TeamBaserunnersLeagueComparison,
    TeamHitsAnalysis,
    TeamHitsLeagueComparison,
    TeamHittingComparisonAnalysis,
    TeamRunsAnalysis,
    TeamRunsLeagueComparison,
    TeamStrikeoutsAnalysis,
    TeamStrikeoutsLeagueComparison,
)
from app.schemas.games import HomeAway

HITS_PER_GAME_CAPTION = "Hits per Game"
STRIKEOUTS_PER_GAME_CAPTION = "Batting Strikeouts per Game"
RUNS_PER_GAME_CAPTION = "Runs Scored per Game"
BASERUNNERS_PER_GAME_CAPTION = "Baserunners per Game"
NORMALIZED_INDEX_CAPTION = "MLB Avg = 100"
NO_LEAGUE_COMPARISON_VALUE = "—"
NO_LEAGUE_COMPARISON_CAPTION = "Comparison unavailable"
LEAGUE_COMPARISON_UNAVAILABLE_NOTE = (
    "MLB comparison unavailable. A complete league-season import is "
    "required before an MLB-wide average can be shown."
)
LEAGUE_STRIKEOUTS_UNAVAILABLE_NOTE = (
    "MLB comparison unavailable. A complete league-season import is required "
    "before an MLB-wide batting strikeout average can be shown."
)
LEAGUE_RUNS_UNAVAILABLE_NOTE = (
    "MLB comparison unavailable. A complete league-season import is required "
    "before an MLB-wide runs-per-game average can be shown."
)
LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE = (
    "MLB comparison unavailable. A complete league-season import is required "
    "before an MLB-wide baserunners average can be shown."
)

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def format_long_date(value: date) -> str:
    """Format a date as ``May 18, 2025``.

    Written out rather than using ``strftime`` because the no-padding day
    directive is platform specific.
    """
    return f"{_MONTHS[value.month - 1]} {value.day}, {value.year}"


def format_short_date(value: date) -> str:
    """Format a date as ``May 8``, for axis ticks that already carry the year."""
    return f"{_MONTHS[value.month - 1][:3]} {value.day}"


def format_matchup(opponent_name: str, home_away: HomeAway) -> str:
    """Describe an opponent the way a box score does: ``vs`` home, ``at`` away."""
    prefix = "vs" if home_away == "home" else "at"
    return f"{prefix} {opponent_name}"


@dataclass(frozen=True)
class SummaryCard:
    """One headline number rendered above the explanation panel."""

    label: str
    value: str
    caption: str


def _format_normalized_index(value: float, *, signed: bool = False) -> str:
    """Format an index to one decimal, omitting a redundant trailing zero.

    The analytics model keeps full precision. Index cards are larger-scale
    glance values, so ``108.0`` reads as ``108`` while a meaningful fractional
    point such as ``108.4`` remains visible. A signed zero is a real result for
    the gap and stays distinct from the unavailable state, which is rendered by
    the route before cards are built.
    """
    if signed and abs(value) < 0.05:
        value = 0.0
    rendered = f"{value:+.1f}" if signed else f"{value:.1f}"
    return rendered.removesuffix(".0")


def build_hitting_comparison_summary_cards(
    analysis: TeamHittingComparisonAnalysis,
) -> list[SummaryCard]:
    """Build the four normalized-comparison cards without judging direction."""
    summary = analysis.summary
    return [
        SummaryCard(
            label="Recent Hits Index",
            value=_format_normalized_index(summary.recent_hits_index),
            caption=NORMALIZED_INDEX_CAPTION,
        ),
        SummaryCard(
            label="Recent K Index",
            value=_format_normalized_index(summary.recent_strikeouts_index),
            caption=NORMALIZED_INDEX_CAPTION,
        ),
        SummaryCard(
            label="Trend Gap",
            value=_format_normalized_index(summary.trend_gap, signed=True),
            caption="Hits Index − K Index",
        ),
        SummaryCard(
            label="Games Played",
            value=str(summary.games_played),
            caption="Completed Games",
        ),
    ]


def build_summary_cards(
    analysis: TeamHitsAnalysis,
    league_comparison: TeamHitsLeagueComparison | None = None,
) -> list[SummaryCard]:
    """Round the analysis for display only; the calculations keep full precision.

    The third card is the team's difference against MLB. Without complete
    league coverage for the season there is no MLB average to compare with, so
    the card reads ``—`` rather than showing a number the data cannot support.

    ``TeamHitsSummary`` still calculates the prior-window comparison, and the
    chart's rolling average still shows the same trend the old ``vs Prior N``
    card described. Only the card was replaced, to keep four cards on the row.
    """
    window = analysis.rolling_window
    summary = analysis.summary

    if league_comparison is None:
        league_card = SummaryCard(
            label="vs MLB",
            value=NO_LEAGUE_COMPARISON_VALUE,
            caption=NO_LEAGUE_COMPARISON_CAPTION,
        )
    else:
        league_card = SummaryCard(
            label="vs MLB",
            value=f"{league_comparison.difference_vs_mlb:+.2f}",
            caption=HITS_PER_GAME_CAPTION,
        )

    return [
        SummaryCard(
            label=f"Recent {window}-Game Avg",
            value=f"{summary.recent_average:.2f}",
            caption=HITS_PER_GAME_CAPTION,
        ),
        SummaryCard(
            label="Season Avg",
            value=f"{summary.season_average:.2f}",
            caption=HITS_PER_GAME_CAPTION,
        ),
        league_card,
        SummaryCard(
            label="Games Played",
            value=f"{summary.games_played}",
            caption="Completed Games",
        ),
    ]


def format_league_comparison_note(
    comparison: TeamHitsLeagueComparison | None,
) -> str:
    """Explain the MLB context on the page, or why there is none.

    The available wording deliberately says "currently stored" and names the
    number of team-game records behind the average. Complete league coverage
    means every team was refreshed, not that the season has finished being
    played, and the sentence must not let a reader conclude otherwise.
    """
    if comparison is None:
        return LEAGUE_COMPARISON_UNAVAILABLE_NOTE

    league = comparison.league
    return (
        f"MLB averaged {league.hits_per_game:.2f} hits per game across the "
        f"{league.team_game_records:,} team-game records currently stored for "
        f"{league.season}, covering {league.teams_represented} teams — total "
        f"hits divided by total team-game records, so a club that has played "
        f"more games counts for more. Complete league coverage means every "
        f"team was refreshed, not that the season has finished being played."
    )


def build_strikeout_summary_cards(
    analysis: TeamStrikeoutsAnalysis,
    league_comparison: TeamStrikeoutsLeagueComparison | None = None,
) -> list[SummaryCard]:
    """Round the analysis for display only; the calculations keep full precision.

    The third card is the team's difference against MLB, matching the hits
    page. Without trustworthy league batting strikeout data for the season
    there is no MLB average to compare with, so the card reads ``—`` rather
    than showing a number the data cannot support. It never reads ``0.00``,
    which is a real value meaning the team matched MLB exactly.

    ``TeamStrikeoutsSummary`` still calculates the prior-window comparison, and
    the chart's rolling average still shows the same trend the old
    ``vs Prior N`` card described. Only the card was replaced, to keep four
    cards on the row.

    The difference is rendered as a plain signed number with no positive or
    negative styling. More batting strikeouts than MLB is not automatically
    worse for every question a reader might be asking, so the page states the
    direction and leaves the judgement to them.
    """
    window = analysis.rolling_window
    summary = analysis.summary

    if league_comparison is None:
        league_card = SummaryCard(
            label="vs MLB",
            value=NO_LEAGUE_COMPARISON_VALUE,
            caption=NO_LEAGUE_COMPARISON_CAPTION,
        )
    else:
        league_card = SummaryCard(
            label="vs MLB",
            value=f"{league_comparison.difference_vs_mlb:+.2f}",
            caption=STRIKEOUTS_PER_GAME_CAPTION,
        )

    return [
        SummaryCard(
            label=f"Recent {window}-Game Avg",
            value=f"{summary.recent_average:.2f}",
            caption=STRIKEOUTS_PER_GAME_CAPTION,
        ),
        SummaryCard(
            label="Season Avg",
            value=f"{summary.season_average:.2f}",
            caption=STRIKEOUTS_PER_GAME_CAPTION,
        ),
        league_card,
        SummaryCard(
            label="Games Played",
            value=f"{summary.games_played}",
            caption="Completed Games",
        ),
    ]


def format_league_strikeouts_note(
    comparison: TeamStrikeoutsLeagueComparison | None,
) -> str:
    """Explain the MLB batting strikeout context, or why there is none.

    The available wording deliberately says "currently stored" and names the
    number of team-game records behind the average. Complete league coverage
    means every team was refreshed, not that the season has finished being
    played, and the sentence must not let a reader conclude otherwise.

    It also says "batting strikeouts" rather than "strikeouts", because a
    per-game strikeout number could otherwise be read as the team's pitching.
    """
    if comparison is None:
        return LEAGUE_STRIKEOUTS_UNAVAILABLE_NOTE

    league = comparison.league
    return (
        f"MLB hitters struck out {league.strikeouts_per_game:.2f} times per "
        f"game across the {league.team_game_records:,} team-game records "
        f"currently stored for {league.season}, covering "
        f"{league.teams_represented} teams — total batting strikeouts divided "
        f"by total team-game records, so a club that has played more games "
        f"counts for more. Complete league coverage means every team was "
        f"refreshed, not that the season has finished being played."
    )


def format_league_strikeouts_backfill_note(
    *,
    season: int,
    records_missing: int,
    records_total: int,
    reimport_command: str,
) -> str:
    """Say that league batting strikeout data is incomplete, and how to fix it.

    Distinct from the coverage wording on purpose. Here the league-wide refresh
    did cover every team, but some stored records predate batting strikeouts
    being persisted. Their totals are unknown, not zero, and an average over
    only the records that carry a value is not an MLB-wide average, so the page
    shows none and asks for a backfill instead.
    """
    # One missing record is as disqualifying as a thousand, so the sentence has
    # to read correctly for both.
    has_have = "has" if records_missing == 1 else "have"
    return (
        f"MLB comparison unavailable. {records_missing:,} of the "
        f"{records_total:,} team-game records stored for {season} {has_have} no "
        f"batting strikeout total, because they were imported before batting "
        f"strikeouts were persisted. They are not counted as zero and the rest "
        f"are not presented as MLB overall. Re-import the league season to "
        f"backfill them: {reimport_command}"
    )


def build_runs_summary_cards(
    analysis: TeamRunsAnalysis,
    league_comparison: TeamRunsLeagueComparison | None = None,
) -> list[SummaryCard]:
    """Round the analysis for display only; the calculations keep full precision.

    The same four cards the hits and batting strikeout pages show. The third is
    the team's difference against MLB. Without complete league coverage for the
    season there is no MLB average to compare with, so the card reads ``—``
    rather than showing a number the data cannot support. It never reads
    ``0.00``, which is a real value meaning the team matched MLB exactly.

    ``TeamRunsSummary`` still calculates the prior-window comparison; it is not
    given a card, so the row keeps four cards rather than growing a fifth.
    """
    window = analysis.rolling_window
    summary = analysis.summary

    if league_comparison is None:
        league_card = SummaryCard(
            label="vs MLB",
            value=NO_LEAGUE_COMPARISON_VALUE,
            caption=NO_LEAGUE_COMPARISON_CAPTION,
        )
    else:
        league_card = SummaryCard(
            label="vs MLB",
            value=f"{league_comparison.difference_vs_mlb:+.2f}",
            caption=RUNS_PER_GAME_CAPTION,
        )

    return [
        SummaryCard(
            label=f"Recent {window}-Game Avg",
            value=f"{summary.recent_average:.2f}",
            caption=RUNS_PER_GAME_CAPTION,
        ),
        SummaryCard(
            label="Season Avg",
            value=f"{summary.season_average:.2f}",
            caption=RUNS_PER_GAME_CAPTION,
        ),
        league_card,
        SummaryCard(
            label="Games Played",
            value=f"{summary.games_played}",
            caption="Completed Games",
        ),
    ]


def format_league_runs_note(
    comparison: TeamRunsLeagueComparison | None,
) -> str:
    """Explain the MLB run-scoring context on the page, or why there is none.

    The available wording deliberately says "currently stored" and names the
    number of team-game records behind the average. Complete league coverage
    means every team was refreshed, not that the season has finished being
    played, and the sentence must not let a reader conclude otherwise.

    It also says "scored" so a per-game run number is not read as runs allowed.
    """
    if comparison is None:
        return LEAGUE_RUNS_UNAVAILABLE_NOTE

    league = comparison.league
    return (
        f"MLB teams scored {league.runs_per_game:.2f} runs per game across the "
        f"{league.team_game_records:,} team-game records currently stored for "
        f"{league.season}, covering {league.teams_represented} teams — total "
        f"runs divided by total team-game records, so a club that has played "
        f"more games counts for more. Complete league coverage means every "
        f"team was refreshed, not that the season has finished being played."
    )


def build_baserunners_summary_cards(
    analysis: TeamBaserunnersAnalysis,
    league_comparison: TeamBaserunnersLeagueComparison | None = None,
) -> list[SummaryCard]:
    """Round the analysis for display only; the calculations keep full precision.

    The same four cards the other metric pages show. The third is the team's
    difference against MLB. Without trustworthy league baserunner data for the
    season there is no MLB average to compare with, so the card reads ``—``
    rather than showing a number the data cannot support. It never reads
    ``0.00``, which is a real value meaning the team matched MLB exactly.

    ``TeamBaserunnersSummary`` still calculates the prior-window comparison; it
    is not given a card, so the row keeps four cards rather than growing a
    fifth.
    """
    window = analysis.rolling_window
    summary = analysis.summary

    if league_comparison is None:
        league_card = SummaryCard(
            label="vs MLB",
            value=NO_LEAGUE_COMPARISON_VALUE,
            caption=NO_LEAGUE_COMPARISON_CAPTION,
        )
    else:
        league_card = SummaryCard(
            label="vs MLB",
            value=f"{league_comparison.difference_vs_mlb:+.2f}",
            caption=BASERUNNERS_PER_GAME_CAPTION,
        )

    return [
        SummaryCard(
            label=f"Recent {window}-Game Avg",
            value=f"{summary.recent_average:.2f}",
            caption=BASERUNNERS_PER_GAME_CAPTION,
        ),
        SummaryCard(
            label="Season Avg",
            value=f"{summary.season_average:.2f}",
            caption=BASERUNNERS_PER_GAME_CAPTION,
        ),
        league_card,
        SummaryCard(
            label="Games Played",
            value=f"{summary.games_played}",
            caption="Completed Games",
        ),
    ]


def format_league_baserunners_note(
    comparison: TeamBaserunnersLeagueComparison | None,
) -> str:
    """Explain the MLB baserunners context on the page, or why there is none.

    The available wording deliberately says "currently stored" and names the
    number of team-game records behind the average. Complete league coverage
    means every team was refreshed, not that the season has finished being
    played, and the sentence must not let a reader conclude otherwise.
    """
    if comparison is None:
        return LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE

    league = comparison.league
    return (
        f"MLB teams put a runner on base {league.baserunners_per_game:.2f} "
        f"times per game across the {league.team_game_records:,} team-game "
        f"records currently stored for {league.season}, covering "
        f"{league.teams_represented} teams — total baserunners (hits + walks + "
        f"hit-by-pitch) divided by total team-game records, so a club that has "
        f"played more games counts for more. Complete league coverage means "
        f"every team was refreshed, not that the season has finished being "
        f"played."
    )


def format_league_baserunners_backfill_note(
    *,
    season: int,
    records_missing: int,
    records_total: int,
    reimport_command: str,
) -> str:
    """Say that league baserunner data is incomplete, and how to fix it.

    Distinct from the coverage wording on purpose. Here the league-wide refresh
    did cover every team, but some stored records predate walks and
    hit-by-pitch being persisted. Their totals are unknown, not zero, and an
    average over only the records that carry a value is not an MLB-wide
    average, so the page shows none and asks for a backfill instead.
    """
    # One missing record is as disqualifying as a thousand, so the sentence has
    # to read correctly for both.
    has_have = "has" if records_missing == 1 else "have"
    return (
        f"MLB comparison unavailable. {records_missing:,} of the "
        f"{records_total:,} team-game records stored for {season} {has_have} no "
        f"walk or hit-by-pitch total, because they were imported before "
        f"baserunner components were persisted. They are not counted as zero "
        f"and the rest are not presented as MLB overall. Re-import the league "
        f"season to backfill them: {reimport_command}"
    )
