"""Plotly figure construction for team hitting visualizations.

Kept out of the route so the figure contract can be tested without HTTP and so
the route stays about request handling.
"""

from functools import lru_cache

import plotly.graph_objects as go
from plotly.io import to_html
from plotly.offline import get_plotlyjs

from app.schemas.analytics import TeamHitsAnalysis
from app.web.formatting import format_long_date, format_matchup

CHART_DIV_ID = "team-hits-chart"
RAW_HITS_TRACE_NAME = "Game Hits"
SEASON_AVERAGE_TRACE_NAME = "Season Average"

X_AXIS_TITLE = "Season Game Number"
Y_AXIS_TITLE = "Hits per Game"

_NAVY = "#12263f"
_TEAL = "#0f8b8d"
_RAW_LINE = "#b7c7d8"
_RAW_MARKER = "#7c93ab"
_GRID = "#e6ebf1"

CHART_CONFIG = {
    "responsive": True,
    "displaylogo": False,
    "displayModeBar": False,
    "scrollZoom": False,
}


def rolling_average_trace_name(rolling_window: int) -> str:
    """Label the rolling average with the window the viewer selected."""
    return f"{rolling_window}-Game Average"


def build_team_hits_figure(analysis: TeamHitsAnalysis) -> go.Figure:
    """Build the hits-per-game figure for one team-season analysis."""
    game_numbers = [point.season_game_number for point in analysis.points]
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
            marker={"color": _RAW_MARKER, "size": 5},
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
            line={"color": _TEAL, "width": 3.5, "shape": "spline", "smoothing": 0.4},
            hovertemplate=hover_template,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[game_numbers[0], game_numbers[-1]],
            y=[analysis.season_average, analysis.season_average],
            name=SEASON_AVERAGE_TRACE_NAME,
            mode="lines",
            line={"color": _NAVY, "width": 2, "dash": "dash"},
            hoverinfo="skip",
        )
    )

    figure.update_layout(
        template="plotly_white",
        # Axis automargin sizes the gutters, which keeps the plot area as wide
        # as possible on a narrow phone screen.
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        height=440,
        hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "system-ui, -apple-system, 'Segoe UI', sans-serif", "size": 13},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 12},
        },
        xaxis={
            "title": {"text": X_AXIS_TITLE, "standoff": 8},
            "gridcolor": _GRID,
            "zeroline": False,
            "rangemode": "tozero",
            "automargin": True,
        },
        yaxis={
            "title": {"text": Y_AXIS_TITLE, "standoff": 8},
            "gridcolor": _GRID,
            "zeroline": False,
            "rangemode": "tozero",
            # Whole numbers of hits; the range still grows with the data.
            "tickformat": "d",
            "dtick": 2,
            "automargin": True,
        },
    )
    return figure


def render_figure_html(figure: go.Figure) -> str:
    """Render a figure as an embeddable div.

    ``plotly.js`` is served by the application rather than inlined here, so the
    HTML stays small and the page works without a CDN.
    """
    return to_html(
        figure,
        full_html=False,
        include_plotlyjs=False,
        config=CHART_CONFIG,
        div_id=CHART_DIV_ID,
    )


@lru_cache(maxsize=1)
def plotly_bundle_javascript() -> str:
    """Return the ``plotly.js`` bundle shipped with the installed plotly package."""
    return get_plotlyjs()
