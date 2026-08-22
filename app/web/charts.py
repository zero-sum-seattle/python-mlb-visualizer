"""Plotly figure construction for team hitting visualizations.

Kept out of the route so the figure contract can be tested without HTTP and so
the route stays about request handling.

The hits, batting strikeout, runs, baserunners, and normalized comparison
figures are built by separate functions that share only the rendering helpers
below. They look alike, but a single parameterized builder would have to
encode which labels, colours, and axis semantics belong to which statistic,
which is harder to read than five explicit builders.
"""

from datetime import date
from functools import lru_cache

import plotly.graph_objects as go
from plotly.io import to_html
from plotly.offline import get_plotlyjs

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
from app.web.formatting import format_long_date, format_matchup, format_short_date

CHART_DIV_ID = "team-hits-chart"
RAW_HITS_TRACE_NAME = "Game Hits"
# Either chart can carry two horizontal reference lines at once, so the team
# line always says whose average it is.
TEAM_SEASON_AVERAGE_TRACE_NAME = "Team Season Average"
MLB_AVERAGE_TRACE_NAME = "MLB Average"

X_AXIS_TITLE = "Season Game Number"
Y_AXIS_TITLE = "Hits per Game"

STRIKEOUTS_CHART_DIV_ID = "team-strikeouts-chart"
# "Batting" is carried through every label so a reader never has to guess
# whether these are strikeouts by the team's hitters or by its pitchers.
RAW_STRIKEOUTS_TRACE_NAME = "Game Strikeouts"
STRIKEOUTS_Y_AXIS_TITLE = "Batting Strikeouts per Game"

RUNS_CHART_DIV_ID = "team-runs-chart"
# Runs scored by the selected team. "Scored" is carried through the axis title
# so a per-game run number cannot be read as runs allowed or as a differential.
RAW_RUNS_TRACE_NAME = "Game Runs"
RUNS_Y_AXIS_TITLE = "Runs Scored per Game"

BASERUNNERS_CHART_DIV_ID = "team-baserunners-chart"
RAW_BASERUNNERS_TRACE_NAME = "Game Baserunners"
BASERUNNERS_Y_AXIS_TITLE = "Baserunners per Game"

COMPARISON_CHART_DIV_ID = "team-hitting-comparison-chart"
HITS_INDEX_TRACE_NAME = "Hits Index"
STRIKEOUTS_INDEX_TRACE_NAME = "Batting Strikeout Index"
NORMALIZED_BASELINE_TRACE_NAME = "Baseline (100)"
COMPARISON_Y_AXIS_TITLE = "Normalized Index (MLB Avg = 100)"

_NAVY = "#12263f"
_TEAL = "#0f8b8d"
# Distinct hue *and* distinct dash from the navy team line, so the two
# reference lines stay apart in greyscale and for a colour-blind reader.
_AMBER = "#b26a00"
_RAW_LINE = "#b7c7d8"
_RAW_MARKER = "#7c93ab"
_GRID = "#dbe2ea"
_AXIS_LINE = "#c9d3de"
_AXIS_INK = "#5b6b7c"
# The right gutter holds the reference-line label; the rest is sized by the
# axes themselves.
_MARGIN = {"l": 8, "r": 78, "t": 8, "b": 8}
# The comparison has three longer legend entries and no right-edge label.
# Its top band lets the horizontal legend wrap on a phone without covering the
# plot. The small right gutter also keeps the final two-line date label inside
# the SVG at 390px rather than letting Plotly hide that boundary tick.
_COMPARISON_MARGIN = {"l": 8, "r": 32, "t": 76, "b": 8}
_AXIS_TITLE_FONT = {"size": 12, "color": _AXIS_INK}
_TICK_FONT = {"size": 11, "color": _AXIS_INK}

CHART_CONFIG = {
    "responsive": True,
    "displaylogo": False,
    "displayModeBar": False,
    "scrollZoom": False,
}


def rolling_average_trace_name(rolling_window: int) -> str:
    """Label the rolling average with the window the viewer selected."""
    return f"{rolling_window}-Game Average"


def _season_game_ticks(
    game_numbers: list[int],
    game_dates: list[date],
) -> tuple[list[int], list[str]]:
    """Label about ten games across the season with their number and date.

    A season game number alone does not tell a reader when in the season a
    stretch happened, so each labelled tick carries the date of that game. The
    last game is always labelled, because where the stored data ends is the
    thing a reader most often wants to place in time.
    """
    count = len(game_numbers)
    step = max(1, round(count / 10))
    indexes = list(range(0, count, step))
    # Replace the final stepped tick when it would crowd the last game rather
    # than adding a label on top of it.
    if count - 1 - indexes[-1] < step / 2:
        indexes[-1] = count - 1
    else:
        indexes.append(count - 1)

    return (
        [game_numbers[index] for index in indexes],
        [
            f"{game_numbers[index]}<br>{format_short_date(game_dates[index])}"
            for index in indexes
        ],
    )


def _label_reference_line(
    figure: go.Figure,
    *,
    x: int,
    y: float,
    name: str,
) -> None:
    """Name a horizontal reference line at the right edge of the plot.

    The legend says which line is which, but the value itself is what a reader
    compares against, and reading it off the axis is guesswork when two
    reference lines sit close together.
    """
    figure.add_annotation(
        x=x,
        y=y,
        text=f"{name}<br>{y:.2f}",
        showarrow=False,
        xanchor="left",
        yanchor="middle",
        xshift=8,
        align="left",
        font={"size": 11, "color": _AXIS_INK},
    )


def build_team_hits_figure(
    analysis: TeamHitsAnalysis,
    league_comparison: TeamHitsLeagueComparison | None = None,
) -> go.Figure:
    """Build the hits-per-game figure for one team-season analysis.

    ``league_comparison`` adds a fourth trace, a horizontal MLB reference line.
    It is optional: a season without complete league coverage has no MLB
    average to draw, and the team's own chart must still render.
    """
    game_numbers = [point.season_game_number for point in analysis.points]
    game_dates = [point.game_date for point in analysis.points]
    hits = [point.hits for point in analysis.points]
    rolling = [point.rolling_average for point in analysis.points]
    # Each hover box shows date, matchup, hits, and rolling average regardless
    # of which trace the pointer is over.
    hover_data = [
        (
            format_long_date(point.game_date),
            format_matchup(point.opponent_name, point.home_away),
            point.hits,
            point.rolling_average,
        )
        for point in analysis.points
    ]
    rolling_name = rolling_average_trace_name(analysis.rolling_window)
    hover_template = (
        "<b>%{customdata[0]}</b><br>"
        "%{customdata[1]}<br>"
        "Hits: %{customdata[2]}<br>"
        f"{analysis.rolling_window}-Game Avg: "
        "%{customdata[3]:.2f}<extra></extra>"
    )

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=hits,
            customdata=hover_data,
            name=RAW_HITS_TRACE_NAME,
            mode="lines+markers",
            line={"color": _RAW_LINE, "width": 1.2},
            # Open circles: the game markers sit on top of each other in a
            # 162-game season, and an outline stays readable where filled
            # dots merge into a blob.
            marker={
                "size": 5,
                "color": "rgba(0,0,0,0)",
                "line": {"color": _RAW_MARKER, "width": 1.2},
            },
            hovertemplate=hover_template,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=rolling,
            customdata=hover_data,
            name=rolling_name,
            mode="lines",
            # Straight segments between calculated points. A spline would
            # overshoot between games and imply averages nobody calculated.
            line={"color": _TEAL, "width": 3.5, "shape": "linear"},
            hovertemplate=hover_template,
        )
    )
    season_average = analysis.summary.season_average
    figure.add_trace(
        go.Scatter(
            x=[game_numbers[0], game_numbers[-1]],
            y=[season_average, season_average],
            name=TEAM_SEASON_AVERAGE_TRACE_NAME,
            mode="lines",
            line={"color": _NAVY, "width": 2, "dash": "dash"},
            hoverinfo="skip",
        )
    )
    if league_comparison is not None:
        mlb_average = league_comparison.league.hits_per_game
        figure.add_trace(
            go.Scatter(
                x=[game_numbers[0], game_numbers[-1]],
                y=[mlb_average, mlb_average],
                name=MLB_AVERAGE_TRACE_NAME,
                mode="lines",
                line={"color": _AMBER, "width": 2, "dash": "dot"},
                hoverinfo="skip",
            )
        )

    # Only one of the two horizontal lines is labelled. They can sit within a
    # tenth of a hit of each other, and two labels there would overlap.
    if league_comparison is None:
        _label_reference_line(
            figure,
            x=game_numbers[-1],
            y=season_average,
            name=TEAM_SEASON_AVERAGE_TRACE_NAME,
        )
    else:
        _label_reference_line(
            figure,
            x=game_numbers[-1],
            y=league_comparison.league.hits_per_game,
            name=MLB_AVERAGE_TRACE_NAME,
        )

    tick_values, tick_labels = _season_game_ticks(game_numbers, game_dates)
    figure.update_layout(
        template="plotly_white",
        # Axis automargin sizes the left and bottom gutters, which keeps the
        # plot area as wide as possible on a narrow phone screen.
        margin=_MARGIN,
        height=470,
        hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "system-ui, -apple-system, 'Segoe UI', sans-serif", "size": 13},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.04,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 12, "color": _AXIS_INK},
        },
        xaxis={
            "title": {"text": X_AXIS_TITLE, "standoff": 10, "font": _AXIS_TITLE_FONT},
            "tickfont": _TICK_FONT,
            "tickmode": "array",
            "tickvals": tick_values,
            "ticktext": tick_labels,
            # Only the horizontal gridlines are drawn: they are what a reader
            # measures a value against, and vertical lines only add noise.
            "showgrid": False,
            "showline": True,
            "linecolor": _AXIS_LINE,
            "zeroline": False,
            "rangemode": "tozero",
            "automargin": True,
        },
        yaxis={
            "title": {
                "text": Y_AXIS_TITLE,
                "standoff": 10,
                "font": _AXIS_TITLE_FONT,
            },
            "tickfont": _TICK_FONT,
            "gridcolor": _GRID,
            "griddash": "dot",
            "zeroline": False,
            "rangemode": "tozero",
            # Whole numbers of hits; the range still grows with the data.
            "tickformat": "d",
            "dtick": 2,
            "automargin": True,
        },
    )
    return figure


def build_team_strikeouts_figure(
    analysis: TeamStrikeoutsAnalysis,
    league_comparison: TeamStrikeoutsLeagueComparison | None = None,
) -> go.Figure:
    """Build the batting-strikeouts-per-game figure for one team-season.

    ``league_comparison`` adds a fourth trace, a horizontal MLB reference line,
    drawn in the same amber dotted style the hits chart uses so the two pages
    read as one application. It is optional: a season without trustworthy
    league batting strikeout data has no MLB average to draw, and the team's
    own chart must still render.
    """
    game_numbers = [point.season_game_number for point in analysis.points]
    game_dates = [point.game_date for point in analysis.points]
    strikeouts = [point.strikeouts for point in analysis.points]
    rolling = [point.rolling_average for point in analysis.points]
    # Each hover box shows date, matchup, batting strikeouts, and rolling
    # average regardless of which trace the pointer is over.
    hover_data = [
        (
            format_long_date(point.game_date),
            format_matchup(point.opponent_name, point.home_away),
            point.strikeouts,
            point.rolling_average,
        )
        for point in analysis.points
    ]
    rolling_name = rolling_average_trace_name(analysis.rolling_window)
    hover_template = (
        "<b>%{customdata[0]}</b><br>"
        "%{customdata[1]}<br>"
        "Batting Strikeouts: %{customdata[2]}<br>"
        f"{analysis.rolling_window}-Game Avg: "
        "%{customdata[3]:.2f}<extra></extra>"
    )

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=strikeouts,
            customdata=hover_data,
            name=RAW_STRIKEOUTS_TRACE_NAME,
            mode="lines+markers",
            line={"color": _RAW_LINE, "width": 1.2},
            # Open circles: the game markers sit on top of each other in a
            # 162-game season, and an outline stays readable where filled
            # dots merge into a blob.
            marker={
                "size": 5,
                "color": "rgba(0,0,0,0)",
                "line": {"color": _RAW_MARKER, "width": 1.2},
            },
            hovertemplate=hover_template,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=rolling,
            customdata=hover_data,
            name=rolling_name,
            mode="lines",
            # Straight segments between calculated points. A spline would
            # overshoot between games and imply averages nobody calculated.
            line={"color": _TEAL, "width": 3.5, "shape": "linear"},
            hovertemplate=hover_template,
        )
    )
    season_average = analysis.summary.season_average
    figure.add_trace(
        go.Scatter(
            x=[game_numbers[0], game_numbers[-1]],
            y=[season_average, season_average],
            name=TEAM_SEASON_AVERAGE_TRACE_NAME,
            mode="lines",
            line={"color": _NAVY, "width": 2, "dash": "dash"},
            hoverinfo="skip",
        )
    )
    if league_comparison is not None:
        mlb_average = league_comparison.league.strikeouts_per_game
        figure.add_trace(
            go.Scatter(
                x=[game_numbers[0], game_numbers[-1]],
                y=[mlb_average, mlb_average],
                name=MLB_AVERAGE_TRACE_NAME,
                mode="lines",
                line={"color": _AMBER, "width": 2, "dash": "dot"},
                hoverinfo="skip",
            )
        )

    # Only one of the two horizontal lines is labelled. They can sit within a
    # tenth of a strikeout of each other, and two labels there would overlap.
    if league_comparison is None:
        _label_reference_line(
            figure,
            x=game_numbers[-1],
            y=season_average,
            name=TEAM_SEASON_AVERAGE_TRACE_NAME,
        )
    else:
        _label_reference_line(
            figure,
            x=game_numbers[-1],
            y=league_comparison.league.strikeouts_per_game,
            name=MLB_AVERAGE_TRACE_NAME,
        )

    tick_values, tick_labels = _season_game_ticks(game_numbers, game_dates)
    figure.update_layout(
        template="plotly_white",
        margin=_MARGIN,
        height=470,
        hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "system-ui, -apple-system, 'Segoe UI', sans-serif", "size": 13},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.04,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 12, "color": _AXIS_INK},
        },
        xaxis={
            "title": {"text": X_AXIS_TITLE, "standoff": 10, "font": _AXIS_TITLE_FONT},
            "tickfont": _TICK_FONT,
            "tickmode": "array",
            "tickvals": tick_values,
            "ticktext": tick_labels,
            # Only the horizontal gridlines are drawn: they are what a reader
            # measures a value against, and vertical lines only add noise.
            "showgrid": False,
            "showline": True,
            "linecolor": _AXIS_LINE,
            "zeroline": False,
            "rangemode": "tozero",
            "automargin": True,
        },
        yaxis={
            "title": {
                "text": STRIKEOUTS_Y_AXIS_TITLE,
                "standoff": 10,
                "font": _AXIS_TITLE_FONT,
            },
            "tickfont": _TICK_FONT,
            "gridcolor": _GRID,
            "griddash": "dot",
            "zeroline": False,
            # Starts at zero like the hits chart, and grows with the data. No
            # fixed maximum: a team that strikes out 20 times must still fit.
            "rangemode": "tozero",
            "tickformat": "d",
            "dtick": 2,
            "automargin": True,
        },
    )
    return figure


def build_team_runs_figure(
    analysis: TeamRunsAnalysis,
    league_comparison: TeamRunsLeagueComparison | None = None,
) -> go.Figure:
    """Build the runs-per-game figure for one team-season.

    ``league_comparison`` adds a fourth trace, a horizontal MLB reference line,
    drawn in the same amber dotted style the hits and batting strikeout charts
    use so the three pages read as one application. It is optional: a season
    without complete league coverage has no MLB average to draw, and the team's
    own chart must still render.
    """
    game_numbers = [point.season_game_number for point in analysis.points]
    game_dates = [point.game_date for point in analysis.points]
    runs = [point.runs for point in analysis.points]
    rolling = [point.rolling_average for point in analysis.points]
    # Each hover box shows date, matchup, runs, and rolling average regardless
    # of which trace the pointer is over.
    hover_data = [
        (
            format_long_date(point.game_date),
            format_matchup(point.opponent_name, point.home_away),
            point.runs,
            point.rolling_average,
        )
        for point in analysis.points
    ]
    rolling_name = rolling_average_trace_name(analysis.rolling_window)
    hover_template = (
        "<b>%{customdata[0]}</b><br>"
        "%{customdata[1]}<br>"
        "Runs: %{customdata[2]}<br>"
        f"{analysis.rolling_window}-Game Avg: "
        "%{customdata[3]:.2f}<extra></extra>"
    )

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=runs,
            customdata=hover_data,
            name=RAW_RUNS_TRACE_NAME,
            mode="lines+markers",
            line={"color": _RAW_LINE, "width": 1.2},
            # Open circles: the game markers sit on top of each other in a
            # 162-game season, and an outline stays readable where filled
            # dots merge into a blob.
            marker={
                "size": 5,
                "color": "rgba(0,0,0,0)",
                "line": {"color": _RAW_MARKER, "width": 1.2},
            },
            hovertemplate=hover_template,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=rolling,
            customdata=hover_data,
            name=rolling_name,
            mode="lines",
            # Straight segments between calculated points. A spline would
            # overshoot between games and imply averages nobody calculated.
            line={"color": _TEAL, "width": 3.5, "shape": "linear"},
            hovertemplate=hover_template,
        )
    )
    season_average = analysis.summary.season_average
    figure.add_trace(
        go.Scatter(
            x=[game_numbers[0], game_numbers[-1]],
            y=[season_average, season_average],
            name=TEAM_SEASON_AVERAGE_TRACE_NAME,
            mode="lines",
            line={"color": _NAVY, "width": 2, "dash": "dash"},
            hoverinfo="skip",
        )
    )
    if league_comparison is not None:
        mlb_average = league_comparison.league.runs_per_game
        figure.add_trace(
            go.Scatter(
                x=[game_numbers[0], game_numbers[-1]],
                y=[mlb_average, mlb_average],
                name=MLB_AVERAGE_TRACE_NAME,
                mode="lines",
                line={"color": _AMBER, "width": 2, "dash": "dot"},
                hoverinfo="skip",
            )
        )

    # Only one of the two horizontal lines is labelled. They can sit within a
    # tenth of a run of each other, and two labels there would overlap.
    if league_comparison is None:
        _label_reference_line(
            figure,
            x=game_numbers[-1],
            y=season_average,
            name=TEAM_SEASON_AVERAGE_TRACE_NAME,
        )
    else:
        _label_reference_line(
            figure,
            x=game_numbers[-1],
            y=league_comparison.league.runs_per_game,
            name=MLB_AVERAGE_TRACE_NAME,
        )

    tick_values, tick_labels = _season_game_ticks(game_numbers, game_dates)
    figure.update_layout(
        template="plotly_white",
        margin=_MARGIN,
        height=470,
        hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "system-ui, -apple-system, 'Segoe UI', sans-serif", "size": 13},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.04,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 12, "color": _AXIS_INK},
        },
        xaxis={
            "title": {"text": X_AXIS_TITLE, "standoff": 10, "font": _AXIS_TITLE_FONT},
            "tickfont": _TICK_FONT,
            "tickmode": "array",
            "tickvals": tick_values,
            "ticktext": tick_labels,
            # Only the horizontal gridlines are drawn: they are what a reader
            # measures a value against, and vertical lines only add noise.
            "showgrid": False,
            "showline": True,
            "linecolor": _AXIS_LINE,
            "zeroline": False,
            "rangemode": "tozero",
            "automargin": True,
        },
        yaxis={
            "title": {
                "text": RUNS_Y_AXIS_TITLE,
                "standoff": 10,
                "font": _AXIS_TITLE_FONT,
            },
            "tickfont": _TICK_FONT,
            "gridcolor": _GRID,
            "griddash": "dot",
            "zeroline": False,
            # Starts at zero like the other charts, and grows with the data. No
            # fixed maximum: a 20-run blowout must still fit.
            "rangemode": "tozero",
            "tickformat": "d",
            "dtick": 2,
            "automargin": True,
        },
    )
    return figure


def build_team_baserunners_figure(
    analysis: TeamBaserunnersAnalysis,
    league_comparison: TeamBaserunnersLeagueComparison | None = None,
) -> go.Figure:
    """Build the baserunners-per-game figure for one team-season.

    ``league_comparison`` adds a fourth trace, a horizontal MLB reference line,
    drawn in the same amber dotted style the other metric charts use so every
    page reads as one application. It is optional: a season without
    trustworthy league baserunner data has no MLB average to draw, and the
    team's own chart must still render.
    """
    game_numbers = [point.season_game_number for point in analysis.points]
    game_dates = [point.game_date for point in analysis.points]
    baserunners = [point.baserunners for point in analysis.points]
    rolling = [point.rolling_average for point in analysis.points]
    # Each hover box shows date, matchup, baserunners, and rolling average
    # regardless of which trace the pointer is over.
    hover_data = [
        (
            format_long_date(point.game_date),
            format_matchup(point.opponent_name, point.home_away),
            point.baserunners,
            point.rolling_average,
        )
        for point in analysis.points
    ]
    rolling_name = rolling_average_trace_name(analysis.rolling_window)
    hover_template = (
        "<b>%{customdata[0]}</b><br>"
        "%{customdata[1]}<br>"
        "Baserunners: %{customdata[2]}<br>"
        f"{analysis.rolling_window}-Game Avg: "
        "%{customdata[3]:.2f}<extra></extra>"
    )

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=baserunners,
            customdata=hover_data,
            name=RAW_BASERUNNERS_TRACE_NAME,
            mode="lines+markers",
            line={"color": _RAW_LINE, "width": 1.2},
            # Open circles: the game markers sit on top of each other in a
            # 162-game season, and an outline stays readable where filled
            # dots merge into a blob.
            marker={
                "size": 5,
                "color": "rgba(0,0,0,0)",
                "line": {"color": _RAW_MARKER, "width": 1.2},
            },
            hovertemplate=hover_template,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=rolling,
            customdata=hover_data,
            name=rolling_name,
            mode="lines",
            # Straight segments between calculated points. A spline would
            # overshoot between games and imply averages nobody calculated.
            line={"color": _TEAL, "width": 3.5, "shape": "linear"},
            hovertemplate=hover_template,
        )
    )
    season_average = analysis.summary.season_average
    figure.add_trace(
        go.Scatter(
            x=[game_numbers[0], game_numbers[-1]],
            y=[season_average, season_average],
            name=TEAM_SEASON_AVERAGE_TRACE_NAME,
            mode="lines",
            line={"color": _NAVY, "width": 2, "dash": "dash"},
            hoverinfo="skip",
        )
    )
    if league_comparison is not None:
        mlb_average = league_comparison.league.baserunners_per_game
        figure.add_trace(
            go.Scatter(
                x=[game_numbers[0], game_numbers[-1]],
                y=[mlb_average, mlb_average],
                name=MLB_AVERAGE_TRACE_NAME,
                mode="lines",
                line={"color": _AMBER, "width": 2, "dash": "dot"},
                hoverinfo="skip",
            )
        )

    # Only one of the two horizontal lines is labelled. They can sit within a
    # tenth of a baserunner of each other, and two labels there would overlap.
    if league_comparison is None:
        _label_reference_line(
            figure,
            x=game_numbers[-1],
            y=season_average,
            name=TEAM_SEASON_AVERAGE_TRACE_NAME,
        )
    else:
        _label_reference_line(
            figure,
            x=game_numbers[-1],
            y=league_comparison.league.baserunners_per_game,
            name=MLB_AVERAGE_TRACE_NAME,
        )

    tick_values, tick_labels = _season_game_ticks(game_numbers, game_dates)
    figure.update_layout(
        template="plotly_white",
        margin=_MARGIN,
        height=470,
        hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "system-ui, -apple-system, 'Segoe UI', sans-serif", "size": 13},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.04,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 12, "color": _AXIS_INK},
        },
        xaxis={
            "title": {"text": X_AXIS_TITLE, "standoff": 10, "font": _AXIS_TITLE_FONT},
            "tickfont": _TICK_FONT,
            "tickmode": "array",
            "tickvals": tick_values,
            "ticktext": tick_labels,
            # Only the horizontal gridlines are drawn: they are what a reader
            # measures a value against, and vertical lines only add noise.
            "showgrid": False,
            "showline": True,
            "linecolor": _AXIS_LINE,
            "zeroline": False,
            "rangemode": "tozero",
            "automargin": True,
        },
        yaxis={
            "title": {
                "text": BASERUNNERS_Y_AXIS_TITLE,
                "standoff": 10,
                "font": _AXIS_TITLE_FONT,
            },
            "tickfont": _TICK_FONT,
            "gridcolor": _GRID,
            "griddash": "dot",
            "zeroline": False,
            # Starts at zero like the other charts, and grows with the data. No
            # fixed maximum: a high-traffic offensive game must still fit.
            "rangemode": "tozero",
            "tickformat": "d",
            "dtick": 2,
            "automargin": True,
        },
    )
    return figure


def build_team_hitting_comparison_figure(
    analysis: TeamHittingComparisonAnalysis,
) -> go.Figure:
    """Build the normalized hits-versus-batting-strikeouts figure.

    Both solid lines are rolling team rates expressed as an index of their own
    MLB-wide rate. The dotted line is the common 100 baseline. Colour separates
    the two metrics, but deliberately does not encode good or bad: an index
    above 100 only means the team recorded more of that metric than MLB.
    """
    game_numbers = [point.season_game_number for point in analysis.points]
    game_dates = [point.game_date for point in analysis.points]
    hits_indexes = [point.hits_index for point in analysis.points]
    strikeout_indexes = [point.strikeouts_index for point in analysis.points]
    hover_data = [
        (
            format_long_date(point.game_date),
            point.opponent_name,
            point.hits_index,
            point.strikeouts_index,
        )
        for point in analysis.points
    ]
    hover_template = (
        "<b>%{customdata[0]}</b><br>"
        "Opponent: %{customdata[1]}<br>"
        "Hits Index: %{customdata[2]:.1f}<br>"
        "Batting Strikeout Index: %{customdata[3]:.1f}<extra></extra>"
    )

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=hits_indexes,
            customdata=hover_data,
            name=HITS_INDEX_TRACE_NAME,
            mode="lines",
            # Straight segments connect values calculated at actual games;
            # smoothing would imply intermediate indexes never calculated.
            line={"color": _TEAL, "width": 3.5, "shape": "linear"},
            hovertemplate=hover_template,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=strikeout_indexes,
            customdata=hover_data,
            name=STRIKEOUTS_INDEX_TRACE_NAME,
            mode="lines",
            line={"color": _NAVY, "width": 3.5, "shape": "linear"},
            hovertemplate=hover_template,
        )
    )
    baseline = analysis.baseline_index
    figure.add_trace(
        go.Scatter(
            x=[game_numbers[0], game_numbers[-1]],
            y=[baseline, baseline],
            name=NORMALIZED_BASELINE_TRACE_NAME,
            mode="lines",
            # The MLB baseline is distinct in hue and line pattern from both
            # team metrics, including when the chart is read in greyscale.
            line={"color": _AMBER, "width": 2, "dash": "dot"},
            hoverinfo="skip",
        )
    )
    tick_values, tick_labels = _season_game_ticks(game_numbers, game_dates)
    figure.update_layout(
        template="plotly_white",
        margin=_COMPARISON_MARGIN,
        height=470,
        hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "system-ui, -apple-system, 'Segoe UI', sans-serif", "size": 13},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.03,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 12, "color": _AXIS_INK},
        },
        xaxis={
            "title": {"text": X_AXIS_TITLE, "standoff": 10, "font": _AXIS_TITLE_FONT},
            "tickfont": _TICK_FONT,
            "tickmode": "array",
            "tickvals": tick_values,
            "ticktext": tick_labels,
            "showgrid": False,
            "showline": True,
            "linecolor": _AXIS_LINE,
            "zeroline": False,
            "rangemode": "tozero",
            "automargin": True,
        },
        yaxis={
            "title": {
                "text": COMPARISON_Y_AXIS_TITLE,
                "standoff": 10,
                "font": _AXIS_TITLE_FONT,
            },
            "tickfont": _TICK_FONT,
            "gridcolor": _GRID,
            "griddash": "dot",
            "zeroline": False,
            # Normalized values usually cluster around 100. Autorange keeps
            # their movement legible instead of forcing an unrelated zero.
            "tickformat": ".0f",
            "automargin": True,
        },
    )
    return figure


def render_figure_html(figure: go.Figure, *, div_id: str = CHART_DIV_ID) -> str:
    """Render a figure as an embeddable div.

    ``plotly.js`` is served by the application rather than inlined here, so the
    HTML stays small and the page works without a CDN.
    """
    return to_html(
        figure,
        full_html=False,
        include_plotlyjs=False,
        config=CHART_CONFIG,
        div_id=div_id,
    )


@lru_cache(maxsize=1)
def plotly_bundle_javascript() -> str:
    """Return the ``plotly.js`` bundle shipped with the installed plotly package."""
    return get_plotlyjs()
