"""Tests for the Plotly figure contract, not for Plotly itself."""

import pytest

from app.analytics.team_hitting import build_team_hits_analysis
from app.web.charts import (
    CHART_CONFIG,
    CHART_DIV_ID,
    RAW_HITS_TRACE_NAME,
    SEASON_AVERAGE_TRACE_NAME,
    X_AXIS_TITLE,
    Y_AXIS_TITLE,
    build_team_hits_figure,
    plotly_bundle_javascript,
    render_figure_html,
    rolling_average_trace_name,
)
from tests.factories import make_season


@pytest.fixture
def figure():
    analysis = build_team_hits_analysis(make_season([8, 4, 12, 6, 9]), rolling_window=5)
    return build_team_hits_figure(analysis)


def test_figure_has_three_traces(figure) -> None:
    assert len(figure.data) == 3


def test_trace_names_describe_the_three_series(figure) -> None:
    assert [trace.name for trace in figure.data] == [
        RAW_HITS_TRACE_NAME,
        "5-Game Average",
        SEASON_AVERAGE_TRACE_NAME,
    ]


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


def test_season_average_trace_is_a_flat_dashed_reference_line(figure) -> None:
    trace = figure.data[2]
    assert list(trace.y) == pytest.approx([7.8, 7.8])
    assert trace.line.dash == "dash"


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
