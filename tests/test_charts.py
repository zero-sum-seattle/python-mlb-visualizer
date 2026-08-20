"""Tests for the Plotly figure contract, not for Plotly itself."""

import pytest

from app.analytics.league_hitting import compare_team_hits_to_league
from app.analytics.team_hitting import build_team_hits_analysis
from app.web.charts import (
    CHART_CONFIG,
    CHART_DIV_ID,
    MLB_AVERAGE_TRACE_NAME,
    RAW_HITS_TRACE_NAME,
    TEAM_SEASON_AVERAGE_TRACE_NAME,
    X_AXIS_TITLE,
    Y_AXIS_TITLE,
    build_team_hits_figure,
    plotly_bundle_javascript,
    render_figure_html,
    rolling_average_trace_name,
)
from tests.factories import make_league_hits_context, make_season


@pytest.fixture
def figure():
    analysis = build_team_hits_analysis(make_season([8, 4, 12, 6, 9]), rolling_window=5)
    return build_team_hits_figure(analysis)


@pytest.fixture
def league_figure():
    """A figure built with MLB context, as a season with complete coverage gets."""
    analysis = build_team_hits_analysis(make_season([8, 4, 12, 6, 9]), rolling_window=5)
    league = make_league_hits_context(total_hits=61, team_game_records=10)
    return build_team_hits_figure(
        analysis, compare_team_hits_to_league(analysis, league)
    )


def test_figure_has_three_traces_without_mlb_context(figure) -> None:
    """No complete league coverage means no MLB line, and a working chart."""
    assert len(figure.data) == 3


def test_trace_names_describe_the_three_series(figure) -> None:
    assert [trace.name for trace in figure.data] == [
        RAW_HITS_TRACE_NAME,
        "5-Game Average",
        TEAM_SEASON_AVERAGE_TRACE_NAME,
    ]


def test_mlb_context_adds_a_fourth_named_trace(league_figure) -> None:
    assert [trace.name for trace in league_figure.data] == [
        RAW_HITS_TRACE_NAME,
        "5-Game Average",
        TEAM_SEASON_AVERAGE_TRACE_NAME,
        MLB_AVERAGE_TRACE_NAME,
    ]


def test_mlb_trace_is_a_flat_straight_reference_line(league_figure) -> None:
    trace = league_figure.data[3]
    assert list(trace.y) == pytest.approx([6.1, 6.1])
    assert list(trace.x) == [1, 5]
    assert trace.line.shape in (None, "linear")
    assert trace.hoverinfo == "skip"


def test_mlb_trace_reads_the_league_context_average(league_figure) -> None:
    """The chart line and the vs MLB card must not be able to disagree."""
    analysis = build_team_hits_analysis(make_season([3, 4, 5, 12]), rolling_window=2)
    league = make_league_hits_context(total_hits=45, team_game_records=6)
    comparison = compare_team_hits_to_league(analysis, league)
    figure = build_team_hits_figure(analysis, comparison)
    assert list(figure.data[3].y) == pytest.approx(
        [comparison.league.hits_per_game] * 2
    )


def test_the_two_reference_lines_are_visually_distinguishable(league_figure) -> None:
    team, mlb = league_figure.data[2], league_figure.data[3]
    assert team.line.dash != mlb.line.dash
    assert team.line.color != mlb.line.color


@pytest.mark.parametrize("window", [5, 10, 15, 30])
def test_rolling_trace_label_reflects_the_selected_window(window: int) -> None:
    analysis = build_team_hits_analysis(make_season([7] * 40), rolling_window=window)
    figure = build_team_hits_figure(analysis)
    assert figure.data[1].name == f"{window}-Game Average"
    assert rolling_average_trace_name(window) == f"{window}-Game Average"


def test_raw_trace_plots_the_game_hits(figure) -> None:
    assert list(figure.data[0].y) == [8, 4, 12, 6, 9]


def test_raw_trace_uses_the_season_game_number_for_x(figure) -> None:
    assert list(figure.data[0].x) == [1, 2, 3, 4, 5]


def test_rolling_trace_plots_the_rolling_average(figure) -> None:
    assert list(figure.data[1].y) == pytest.approx([8.0, 6.0, 8.0, 7.5, 7.8])


def test_rolling_trace_joins_calculated_points_with_straight_segments(figure) -> None:
    """Splines would draw averages between games that were never calculated."""
    assert figure.data[1].line.shape == "linear"
    assert figure.data[1].line.smoothing is None


def test_rolling_trace_is_still_the_dominant_line(figure) -> None:
    assert figure.data[1].line.width > figure.data[0].line.width


def test_season_average_trace_is_a_flat_dashed_reference_line(figure) -> None:
    trace = figure.data[2]
    assert list(trace.y) == pytest.approx([7.8, 7.8])
    assert trace.line.dash == "dash"


def test_season_average_trace_reads_the_summary_value() -> None:
    """The chart and the summary card must not be able to disagree."""
    analysis = build_team_hits_analysis(make_season([3, 4, 5, 12]), rolling_window=2)
    figure = build_team_hits_figure(analysis)
    assert list(figure.data[2].y) == pytest.approx(
        [analysis.summary.season_average] * 2
    )


def test_season_average_trace_spans_the_whole_season(figure) -> None:
    assert list(figure.data[2].x) == [1, 5]


def test_axis_titles_name_the_baseball_quantities(figure) -> None:
    assert figure.layout.xaxis.title.text == X_AXIS_TITLE
    assert figure.layout.yaxis.title.text == Y_AXIS_TITLE


def test_y_axis_uses_integer_ticks_without_a_hardcoded_maximum(figure) -> None:
    assert figure.layout.yaxis.tickformat == "d"
    assert figure.layout.yaxis.range is None


def test_hover_data_carries_the_date_and_matchup(figure) -> None:
    first, second = figure.data[0].customdata[0], figure.data[0].customdata[1]
    assert first[0] == "March 27, 2025"
    assert first[1] == "vs Minnesota Twins"
    assert second[1] == "at Minnesota Twins"


def test_hover_template_shows_hits_and_the_rolling_average(figure) -> None:
    template = figure.data[0].hovertemplate
    assert "Hits: %{customdata[2]}" in template
    assert "5-Game Avg: %{customdata[3]:.2f}" in template


def test_rolling_trace_shares_the_hover_content(figure) -> None:
    assert figure.data[1].hovertemplate == figure.data[0].hovertemplate


def test_season_average_trace_has_no_hover(figure) -> None:
    assert figure.data[2].hoverinfo == "skip"


def test_chart_config_is_responsive_and_unbranded() -> None:
    assert CHART_CONFIG["responsive"] is True
    assert CHART_CONFIG["displaylogo"] is False


def test_rendered_html_is_an_embeddable_div_without_the_library(figure) -> None:
    html = render_figure_html(figure)
    assert f'id="{CHART_DIV_ID}"' in html
    assert "<html" not in html
    assert "Plotly.newPlot" in html


def test_plotly_bundle_is_served_from_the_installed_package() -> None:
    assert "Plotly" in plotly_bundle_javascript()
