"""Tests for the run differential figure contract, not for Plotly itself.

This figure departs from the other four in two ways that the tests below pin
down: it draws diverging bars split by outcome instead of a marker line, and
its y axis must hold negative values rather than anchoring at zero.
"""

import pytest

from app.analytics.team_run_differential import build_team_run_differential_analysis
from app.web.charts import (
    LOSS_MARGIN_TRACE_NAME,
    RUN_DIFFERENTIAL_CHART_DIV_ID,
    RUN_DIFFERENTIAL_Y_AXIS_TITLE,
    TEAM_SEASON_AVERAGE_TRACE_NAME,
    WIN_MARGIN_TRACE_NAME,
    X_AXIS_TITLE,
    build_team_run_differential_figure,
    render_figure_html,
    rolling_average_trace_name,
)
from tests.factories import make_run_result_season

SCORED = [6, 2, 8, 1, 5]
ALLOWED = [3, 7, 1, 9, 2]


def analysis_for(scored=SCORED, allowed=ALLOWED, window: int = 5):
    return build_team_run_differential_analysis(
        make_run_result_season(scored, allowed), rolling_window=window
    )


@pytest.fixture
def figure():
    return build_team_run_differential_figure(analysis_for())


def trace_named(figure, name):
    for trace in figure.data:
        if trace.name == name:
            return trace
    raise AssertionError(f"No trace named {name!r} in {[t.name for t in figure.data]}")


def test_the_figure_has_wins_losses_rolling_and_season_average(figure) -> None:
    assert [trace.name for trace in figure.data] == [
        WIN_MARGIN_TRACE_NAME,
        LOSS_MARGIN_TRACE_NAME,
        rolling_average_trace_name(5),
        TEAM_SEASON_AVERAGE_TRACE_NAME,
    ]


def test_wins_and_losses_are_split_into_separate_bar_traces(figure) -> None:
    wins = trace_named(figure, WIN_MARGIN_TRACE_NAME)
    losses = trace_named(figure, LOSS_MARGIN_TRACE_NAME)

    assert wins.type == "bar"
    assert losses.type == "bar"
    # 6-3, 8-1 and 5-2 are wins; 2-7 and 1-9 are losses.
    assert list(wins.y) == [3, 7, 3]
    assert list(losses.y) == [-5, -8]


def test_every_game_appears_in_exactly_one_bar_trace(figure) -> None:
    wins = trace_named(figure, WIN_MARGIN_TRACE_NAME)
    losses = trace_named(figure, LOSS_MARGIN_TRACE_NAME)

    plotted = sorted([*wins.x, *losses.x])
    assert plotted == [1, 2, 3, 4, 5]


def test_the_bars_overlay_rather_than_stack(figure) -> None:
    """Each game has one bar; stacking or grouping would misplace it on the x axis."""
    assert figure.layout.barmode == "overlay"


def test_wins_and_losses_are_drawn_in_different_colours(figure) -> None:
    wins = trace_named(figure, WIN_MARGIN_TRACE_NAME)
    losses = trace_named(figure, LOSS_MARGIN_TRACE_NAME)

    assert wins.marker.color != losses.marker.color


def test_the_y_axis_is_not_anchored_at_zero(figure) -> None:
    """The whole point: anchoring at zero would clip every loss off the chart."""
    assert figure.layout.yaxis.rangemode != "tozero"


def test_the_zero_line_is_drawn_and_darker_than_the_grid(figure) -> None:
    """Zero is the win/loss boundary here, not an arbitrary axis end."""
    assert figure.layout.yaxis.zeroline is True
    assert figure.layout.yaxis.zerolinecolor != figure.layout.yaxis.gridcolor


def test_a_losing_season_still_plots_its_bars(figure) -> None:
    losing = build_team_run_differential_figure(
        analysis_for([0, 1, 2], [8, 6, 9], window=3)
    )
    losses = trace_named(losing, LOSS_MARGIN_TRACE_NAME)
    wins = trace_named(losing, WIN_MARGIN_TRACE_NAME)

    assert list(losses.y) == [-8, -5, -7]
    assert list(wins.y) == []


def test_the_season_average_line_spans_the_season(figure) -> None:
    average = trace_named(figure, TEAM_SEASON_AVERAGE_TRACE_NAME)

    assert list(average.x) == [1, 5]
    # 22 scored, 22 allowed across the five games: a dead-even season.
    assert list(average.y) == [0.0, 0.0]


def test_there_is_no_mlb_reference_trace(figure) -> None:
    """League-wide run differential is zero by construction: nothing to draw."""
    assert "MLB Average" not in [trace.name for trace in figure.data]


def test_the_rolling_trace_follows_the_analysis(figure) -> None:
    analysis = analysis_for()
    rolling = trace_named(figure, rolling_average_trace_name(5))

    assert list(rolling.y) == pytest.approx(
        [point.rolling_average for point in analysis.points]
    )


def test_the_axes_are_titled(figure) -> None:
    assert figure.layout.xaxis.title.text == X_AXIS_TITLE
    assert figure.layout.yaxis.title.text == RUN_DIFFERENTIAL_Y_AXIS_TITLE


def test_the_hover_shows_the_score_and_a_signed_differential(figure) -> None:
    wins = trace_named(figure, WIN_MARGIN_TRACE_NAME)

    first_win = wins.customdata[0]
    # (date, matchup, W/L, winner runs, loser runs, signed differential, rolling)
    assert first_win[2] == "W"
    assert (first_win[3], first_win[4]) == (6, 3)
    assert first_win[5] == "+3"


def test_a_loss_hover_reads_high_low_with_an_l_flag(figure) -> None:
    losses = trace_named(figure, LOSS_MARGIN_TRACE_NAME)

    first_loss = losses.customdata[0]
    assert first_loss[2] == "L"
    # 2-7 shown as 7-2 with the L flag, the way a box score reads.
    assert (first_loss[3], first_loss[4]) == (7, 2)
    assert first_loss[5] == "-5"


def test_the_figure_renders_into_the_expected_div(figure) -> None:
    html = render_figure_html(figure, div_id=RUN_DIFFERENTIAL_CHART_DIV_ID)

    assert RUN_DIFFERENTIAL_CHART_DIV_ID in html


def test_a_one_game_season_renders(figure) -> None:
    single = build_team_run_differential_figure(analysis_for([4], [1], window=15))

    wins = trace_named(single, WIN_MARGIN_TRACE_NAME)
    assert list(wins.y) == [3]
