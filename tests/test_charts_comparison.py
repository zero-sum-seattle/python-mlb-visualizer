"""Tests for the normalized hits-versus-strikeouts Plotly contract."""

from datetime import date, timedelta

import pytest

from app.schemas.analytics import (
    TeamHittingComparisonAnalysis,
    TeamHittingComparisonPoint,
    TeamHittingComparisonSummary,
)
from app.web.charts import (
    COMPARISON_CHART_DIV_ID,
    COMPARISON_Y_AXIS_TITLE,
    HITS_INDEX_TRACE_NAME,
    NORMALIZED_BASELINE_TRACE_NAME,
    STRIKEOUTS_INDEX_TRACE_NAME,
    X_AXIS_TITLE,
    build_team_hitting_comparison_figure,
    render_figure_html,
)


@pytest.fixture
def analysis() -> TeamHittingComparisonAnalysis:
    hits_indexes = [100.0, 112.5, 125.0, 116.67, 112.5]
    strikeouts_indexes = [100.0, 90.0, 80.0, 86.67, 90.0]
    opening_day = date(2025, 3, 27)
    points = tuple(
        TeamHittingComparisonPoint(
            game_pk=2025000 + index,
            season_game_number=index,
            game_date=opening_day + timedelta(days=index - 1),
            opponent_name="Minnesota Twins",
            hits_index=hits_index,
            strikeouts_index=strikeouts_index,
        )
        for index, (hits_index, strikeouts_index) in enumerate(
            zip(hits_indexes, strikeouts_indexes, strict=True), start=1
        )
    )
    return TeamHittingComparisonAnalysis(
        team_id=136,
        team_name="Seattle Mariners",
        season=2025,
        rolling_window=3,
        mlb_hits_per_game=8.0,
        mlb_strikeouts_per_game=10.0,
        baseline_index=100.0,
        points=points,
        summary=TeamHittingComparisonSummary(
            games_played=5,
            recent_hits_index=112.5,
            recent_strikeouts_index=90.0,
            trend_gap=22.5,
        ),
    )


@pytest.fixture
def figure(analysis: TeamHittingComparisonAnalysis):
    return build_team_hitting_comparison_figure(analysis)


def test_figure_has_the_three_required_traces_in_order(figure) -> None:
    assert [trace.name for trace in figure.data] == [
        HITS_INDEX_TRACE_NAME,
        STRIKEOUTS_INDEX_TRACE_NAME,
        NORMALIZED_BASELINE_TRACE_NAME,
    ]


def test_metric_traces_plot_the_calculated_indexes(figure) -> None:
    assert list(figure.data[0].y) == pytest.approx([100.0, 112.5, 125.0, 116.67, 112.5])
    assert list(figure.data[1].y) == pytest.approx([100.0, 90.0, 80.0, 86.67, 90.0])


def test_metric_traces_use_the_season_game_number(figure) -> None:
    assert list(figure.data[0].x) == [1, 2, 3, 4, 5]
    assert list(figure.data[1].x) == [1, 2, 3, 4, 5]


def test_baseline_is_a_flat_100_line_across_the_stored_games(figure) -> None:
    baseline = figure.data[2]
    assert list(baseline.x) == [1, 5]
    assert list(baseline.y) == pytest.approx([100.0, 100.0])
    assert baseline.hoverinfo == "skip"


def test_baseline_is_visually_distinct_from_both_metric_traces(figure) -> None:
    hits, strikeouts, baseline = figure.data
    assert baseline.line.dash == "dot"
    assert hits.line.dash != baseline.line.dash
    assert strikeouts.line.dash != baseline.line.dash
    assert baseline.line.color not in {hits.line.color, strikeouts.line.color}


def test_metric_lines_are_distinct_and_do_not_use_spline_smoothing(figure) -> None:
    hits, strikeouts = figure.data[:2]
    assert hits.line.color != strikeouts.line.color
    assert hits.line.shape == "linear"
    assert strikeouts.line.shape == "linear"
    assert hits.line.smoothing is None
    assert strikeouts.line.smoothing is None


def test_axis_titles_explain_the_index_baseline(figure) -> None:
    assert figure.layout.xaxis.title.text == X_AXIS_TITLE
    assert figure.layout.yaxis.title.text == COMPARISON_Y_AXIS_TITLE
    assert figure.layout.yaxis.title.text == "Normalized Index (MLB Avg = 100)"


def test_normalized_axis_does_not_force_zero_or_a_hardcoded_range(figure) -> None:
    assert figure.layout.yaxis.rangemode is None
    assert figure.layout.yaxis.range is None


def test_x_axis_ticks_include_the_game_date(figure) -> None:
    assert figure.layout.xaxis.tickvals[0] == 1
    assert figure.layout.xaxis.ticktext[0] == "1<br>Mar 27"
    assert figure.layout.xaxis.tickvals[-1] == 5


def test_hover_names_both_indexes_without_directional_judgment(figure) -> None:
    template = figure.data[0].hovertemplate
    assert "Hits Index: %{customdata[2]:.1f}" in template
    assert "Batting Strikeout Index: %{customdata[3]:.1f}" in template
    assert "good" not in template.lower()
    assert "bad" not in template.lower()
    assert figure.data[1].hovertemplate == template


def test_hover_data_carries_the_date_and_opponent(figure) -> None:
    first = figure.data[0].customdata[0]
    assert first[0] == "March 27, 2025"
    assert first[1] == "Minnesota Twins"


def test_comparison_layout_leaves_mobile_room_for_legend_and_last_tick(figure) -> None:
    assert figure.layout.annotations in (None, ())
    assert figure.layout.margin.r >= 30
    assert figure.layout.margin.t >= 70


def test_chart_heading_remains_a_server_rendered_concern(figure) -> None:
    """The existing pages put their chart heading in Jinja, outside Plotly."""
    assert figure.layout.title.text is None


def test_rendered_html_uses_the_comparison_div_id(figure) -> None:
    html = render_figure_html(figure, div_id=COMPARISON_CHART_DIV_ID)
    assert f'id="{COMPARISON_CHART_DIV_ID}"' in html
    assert "Plotly.newPlot" in html
    assert "plotly.js" not in html.lower()
