"""Plotly figure construction for team hitting visualizations.

Kept out of the route so the figure contract can be tested without HTTP and so
the route stays about request handling.

The hits, batting strikeout, runs, baserunners, run differential, and
normalized comparison figures are built by separate functions that share only
the rendering helpers below. They look alike, but a single parameterized
builder would have to encode which labels, colours, and axis semantics belong
to which statistic, which is harder to read than six explicit builders. The run
differential figure is the clearest case for keeping them apart: it is the only
signed metric, so it is the only one that must not anchor its y axis at zero.
"""

from datetime import date
from functools import lru_cache

import plotly.graph_objects as go
from plotly.io import to_html
from plotly.offline import get_plotlyjs

from app.analytics.team_pitching import build_pitch_count_points
from app.schemas.analytics import (
    TeamBaserunnersAnalysis,
    TeamBaserunnersLeagueComparison,
    TeamHitsAllowedAnalysis,
    TeamHitsAllowedLeagueComparison,
    TeamHitsAnalysis,
    TeamHitsLeagueComparison,
    TeamHittingComparisonAnalysis,
    TeamPitchingAnalysis,
    TeamRunDifferentialAnalysis,
    TeamRunDifferentialPoint,
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

HITS_ALLOWED_CHART_DIV_ID = "team-hits-allowed-chart"
RAW_HITS_ALLOWED_TRACE_NAME = "Game Hits Allowed"
HITS_ALLOWED_Y_AXIS_TITLE = "Hits Allowed per Game"

PITCHING_CHART_DIV_ID = "team-pitching-chart"
RAW_PITCHES_TRACE_NAME = "Game Pitches"
PITCHING_Y_AXIS_TITLE = "Pitches per Game"

RUN_DIFFERENTIAL_CHART_DIV_ID = "team-run-differential-chart"
WIN_MARGIN_TRACE_NAME = "Win Margin"
LOSS_MARGIN_TRACE_NAME = "Loss Margin"
RUN_DIFFERENTIAL_Y_AXIS_TITLE = "Run Differential"

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
# Win and loss margins on the run differential chart. Teal already means "the
# team's own trend" across every page, so wins keep it; the losses are a warm
# red that stays distinguishable from the amber MLB reference used elsewhere.
_WIN_BAR = "#3f9c9d"
_LOSS_BAR = "#c2544d"
_ZERO_LINE = "#8a99a8"
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


def build_team_hits_allowed_figure(
    analysis: TeamHitsAllowedAnalysis,
    league_comparison: TeamHitsAllowedLeagueComparison | None = None,
) -> go.Figure:
    """Build the hits-allowed-per-game figure for one team-season.

    The mirror of the hits chart, drawn identically because hits allowed is the
    same kind of quantity seen from the other side: a count per game, with a
    rolling mean and a dashed season average.

    ``league_comparison`` adds the dotted amber MLB reference line. Unlike the
    ERA comparison on ``/pitching``, it is usually available: the league totals
    for hits and hits allowed are identical, so the MLB side comes from the
    batting table and needs only complete batting coverage.

    One reading note the hits chart does not need: **lower is better** here.
    Nothing in the figure encodes that, so the page says it in text.
    """
    game_numbers = [point.season_game_number for point in analysis.points]
    game_dates = [point.game_date for point in analysis.points]
    hits_allowed = [point.hits_allowed for point in analysis.points]
    rolling = [point.rolling_average for point in analysis.points]
    hover_data = [
        (
            format_long_date(point.game_date),
            format_matchup(point.opponent_name, point.home_away),
            point.hits_allowed,
            point.innings_pitched_display,
            point.rolling_average,
        )
        for point in analysis.points
    ]
    rolling_name = rolling_average_trace_name(analysis.rolling_window)
    hover_template = (
        "<b>%{customdata[0]}</b><br>"
        "%{customdata[1]}<br>"
        "%{customdata[2]} hits allowed over %{customdata[3]} IP<br>"
        f"{analysis.rolling_window}-Game Avg: "
        "%{customdata[4]:.2f}<extra></extra>"
    )

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=hits_allowed,
            customdata=hover_data,
            name=RAW_HITS_ALLOWED_TRACE_NAME,
            mode="lines+markers",
            line={"color": _RAW_LINE, "width": 1.2},
            # Open circles: the game markers sit on top of each other across a
            # 162-game season, and an outline stays readable where filled dots
            # merge into a blob.
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
            "showgrid": False,
            "showline": True,
            "linecolor": _AXIS_LINE,
            "zeroline": False,
            "rangemode": "tozero",
            "automargin": True,
        },
        yaxis={
            "title": {
                "text": HITS_ALLOWED_Y_AXIS_TITLE,
                "standoff": 10,
                "font": _AXIS_TITLE_FONT,
            },
            "tickfont": _TICK_FONT,
            "gridcolor": _GRID,
            "griddash": "dot",
            "zeroline": False,
            # A no-hitter is a real 0, so the axis starts at zero and grows
            # with the data, exactly as the hits chart does.
            "rangemode": "tozero",
            "tickformat": "d",
            "dtick": 2,
            "automargin": True,
        },
    )
    return figure


def build_team_pitching_figure(
    analysis: TeamPitchingAnalysis,
) -> go.Figure:
    """Build the pitches-per-game figure for one team-season.

    Pitches thrown is a **count**, not a rate, so this chart follows the same
    shape as the hits and runs pages: open markers for each game, a rolling
    trailing mean, and a dashed season average. The rate statistics this page
    also reports — ERA, WHIP, K/9, BB/9 — are aggregated quite differently and
    live in the summary cards rather than on this axis.

    There is no MLB reference line. A league-wide pitches-per-game average
    would need every club's pitching lines imported, which is a much larger
    import than the batting-only one most seasons currently have, so the page
    does not promise a comparison it usually could not honour.
    """
    game_numbers = [point.season_game_number for point in analysis.points]
    game_dates = [point.game_date for point in analysis.points]
    pitches, rolling = build_pitch_count_points(analysis)
    hover_data = [
        (
            format_long_date(point.game_date),
            format_matchup(point.opponent_name, point.home_away),
            point.number_of_pitches,
            point.innings_pitched_display,
            point.strikes,
            rolling_value,
        )
        for point, rolling_value in zip(analysis.points, rolling, strict=True)
    ]
    rolling_name = rolling_average_trace_name(analysis.rolling_window)
    hover_template = (
        "<b>%{customdata[0]}</b><br>"
        "%{customdata[1]}<br>"
        "%{customdata[2]} pitches over %{customdata[3]} IP<br>"
        "%{customdata[4]} strikes<br>"
        f"{analysis.rolling_window}-Game Avg: "
        "%{customdata[5]:.1f}<extra></extra>"
    )

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=list(pitches),
            customdata=hover_data,
            name=RAW_PITCHES_TRACE_NAME,
            mode="lines+markers",
            line={"color": _RAW_LINE, "width": 1.2},
            # Open circles: the game markers sit on top of each other across a
            # 162-game season, and an outline stays readable where filled dots
            # merge into a blob.
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
            y=list(rolling),
            customdata=hover_data,
            name=rolling_name,
            mode="lines",
            # Straight segments between calculated points. A spline would
            # overshoot between games and imply averages nobody calculated.
            line={"color": _TEAL, "width": 3.5, "shape": "linear"},
            hovertemplate=hover_template,
        )
    )
    season_average = analysis.summary.season.pitches_per_game
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
    _label_reference_line(
        figure,
        x=game_numbers[-1],
        y=season_average,
        name=TEAM_SEASON_AVERAGE_TRACE_NAME,
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
            "showgrid": False,
            "showline": True,
            "linecolor": _AXIS_LINE,
            "zeroline": False,
            "rangemode": "tozero",
            "automargin": True,
        },
        yaxis={
            "title": {
                "text": PITCHING_Y_AXIS_TITLE,
                "standoff": 10,
                "font": _AXIS_TITLE_FONT,
            },
            "tickfont": _TICK_FONT,
            "gridcolor": _GRID,
            "griddash": "dot",
            "zeroline": False,
            # A team throws roughly 100 pitches at minimum, so anchoring at
            # zero would waste the bottom third of the plot on empty space.
            "rangemode": "normal",
            "tickformat": "d",
            "automargin": True,
        },
    )
    return figure


def build_team_run_differential_figure(
    analysis: TeamRunDifferentialAnalysis,
) -> go.Figure:
    """Build the run differential figure for one team-season.

    Two things make this chart deliberately unlike the other four.

    It uses **diverging bars** rather than a line of open markers. Run
    differential is the only signed metric in the application, and a bar
    growing up or down from a zero baseline shows the sign at a glance in a way
    a line through a cloud of markers does not. The bars are split into two
    traces, wins and losses, so the legend explains the colours and a reader
    can isolate either one.

    It also has **no MLB reference line**, which is not an omission. League-wide
    run differential is exactly zero by construction: every run scored by one
    team is a run allowed by another, so the MLB total cancels. The zero line
    the chart already draws *is* the league average, and a second amber line on
    top of it would say the same thing twice.
    """
    game_numbers = [point.season_game_number for point in analysis.points]
    game_dates = [point.game_date for point in analysis.points]
    rolling = [point.rolling_average for point in analysis.points]
    rolling_name = rolling_average_trace_name(analysis.rolling_window)

    hover_template = (
        "<b>%{customdata[0]}</b><br>"
        "%{customdata[1]}<br>"
        "%{customdata[2]} %{customdata[3]}-%{customdata[4]}<br>"
        "Run Differential: %{customdata[5]}<br>"
        f"{analysis.rolling_window}-Game Avg: "
        "%{customdata[6]:.2f}<extra></extra>"
    )

    def hover_row(
        point: TeamRunDifferentialPoint,
    ) -> tuple[str, str, str, int, int, str, float]:
        # Scores read high-low the way a box score does, so a 7-2 win and a
        # 2-7 loss are told apart by the W/L flag rather than by field order.
        winner_runs = max(point.runs_scored, point.runs_allowed)
        loser_runs = min(point.runs_scored, point.runs_allowed)
        return (
            format_long_date(point.game_date),
            format_matchup(point.opponent_name, point.home_away),
            "W" if point.is_win else "L",
            winner_runs,
            loser_runs,
            # Explicit sign: "+3" and "-3" are opposite outcomes and the plus
            # is what stops a reader scanning the column from missing it.
            f"{point.run_differential:+d}",
            point.rolling_average,
        )

    figure = go.Figure()
    for trace_name, colour, wanted in (
        (WIN_MARGIN_TRACE_NAME, _WIN_BAR, True),
        (LOSS_MARGIN_TRACE_NAME, _LOSS_BAR, False),
    ):
        selected = [point for point in analysis.points if point.is_win is wanted]
        figure.add_trace(
            go.Bar(
                x=[point.season_game_number for point in selected],
                y=[point.run_differential for point in selected],
                customdata=[hover_row(point) for point in selected],
                name=trace_name,
                marker={"color": colour, "line": {"width": 0}},
                hovertemplate=hover_template,
            )
        )

    figure.add_trace(
        go.Scatter(
            x=game_numbers,
            y=rolling,
            customdata=[hover_row(point) for point in analysis.points],
            name=rolling_name,
            mode="lines",
            # Straight segments between calculated points. A spline would
            # overshoot between games and imply averages nobody calculated.
            line={"color": _NAVY, "width": 3.5, "shape": "linear"},
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
            line={"color": _AMBER, "width": 2, "dash": "dash"},
            hoverinfo="skip",
        )
    )
    _label_reference_line(
        figure,
        x=game_numbers[-1],
        y=season_average,
        name=TEAM_SEASON_AVERAGE_TRACE_NAME,
    )

    tick_values, tick_labels = _season_game_ticks(game_numbers, game_dates)
    figure.update_layout(
        template="plotly_white",
        margin=_MARGIN,
        height=470,
        hovermode="closest",
        # The two bar traces are one series split by outcome, not two series to
        # be stacked or placed side by side: every game has exactly one bar.
        barmode="overlay",
        bargap=0.15,
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
            "showgrid": False,
            "showline": False,
            "zeroline": False,
            "rangemode": "tozero",
            "automargin": True,
        },
        yaxis={
            "title": {
                "text": RUN_DIFFERENTIAL_Y_AXIS_TITLE,
                "standoff": 10,
                "font": _AXIS_TITLE_FONT,
            },
            "tickfont": _TICK_FONT,
            "gridcolor": _GRID,
            "griddash": "dot",
            # The one chart in the application that draws its zero line, and
            # draws it darker than the grid. Zero is the win/loss boundary
            # here, not an arbitrary axis end.
            "zeroline": True,
            "zerolinecolor": _ZERO_LINE,
            "zerolinewidth": 1.5,
            # Emphatically not "tozero": the axis has to hold negative values,
            # and anchoring it at zero would clip every loss off the chart.
            "rangemode": "normal",
            "tickformat": "d",
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
