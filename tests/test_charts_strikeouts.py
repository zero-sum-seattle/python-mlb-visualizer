"""Tests for the batting strikeout figure contract, not for Plotly itself."""

import pytest

from app.analytics.league_strikeouts import compare_team_strikeouts_to_league
from app.analytics.team_strikeouts import build_team_strikeouts_analysis
from app.web.charts import (
    MLB_AVERAGE_TRACE_NAME,
    RAW_STRIKEOUTS_TRACE_NAME,
    STRIKEOUTS_CHART_DIV_ID,
    STRIKEOUTS_Y_AXIS_TITLE,
    TEAM_SEASON_AVERAGE_TRACE_NAME,
    X_AXIS_TITLE,
    build_team_strikeouts_figure,
    render_figure_html,
    rolling_average_trace_name,
)
from tests.factories import make_league_strikeouts_context, make_season

VALUES = [10, 4, 14, 6, 11]


def analysis_for(strikeouts: list[int], window: int = 5):
    return build_team_strikeouts_analysis(
        make_season(hits=[8] * len(strikeouts), strikeouts=strikeouts),
        rolling_window=window,
    )


@pytest.fixture
def figure():
    return build_team_strikeouts_figure(analysis_for(VALUES))


@pytest.fixture
def league_figure():
    """A figure built with MLB context, as a trustworthy season gets."""
    analysis = analysis_for(VALUES)
    league = make_league_strikeouts_context(total_strikeouts=82, team_game_records=10)
    return build_team_strikeouts_figure(
        analysis, compare_team_strikeouts_to_league(analysis, league)
    )


def test_figure_has_three_traces_without_mlb_context(figure) -> None:
    """No trustworthy league data means no MLB line, and a working chart."""
    assert len(figure.data) == 3


def test_trace_names_describe_the_three_series(figure) -> None:
    assert [trace.name for trace in figure.data] == [
        RAW_STRIKEOUTS_TRACE_NAME,
        "5-Game Average",
        TEAM_SEASON_AVERAGE_TRACE_NAME,
    ]


def test_mlb_context_adds_a_fourth_named_trace(league_figure) -> None:
    assert [trace.name for trace in league_figure.data] == [
        RAW_STRIKEOUTS_TRACE_NAME,
        "5-Game Average",
        TEAM_SEASON_AVERAGE_TRACE_NAME,
        MLB_AVERAGE_TRACE_NAME,
    ]


def test_no_mlb_trace_without_a_comparison(figure) -> None:
    assert MLB_AVERAGE_TRACE_NAME not in [trace.name for trace in figure.data]


def test_the_mlb_trace_plots_the_league_average(league_figure) -> None:
    assert list(league_figure.data[3].y) == pytest.approx([8.2, 8.2])


def test_the_mlb_trace_spans_the_whole_season(league_figure) -> None:
    assert list(league_figure.data[3].x) == [1, 5]


def test_the_mlb_trace_is_dotted_so_the_two_reference_lines_differ(
    league_figure,
) -> None:
    assert league_figure.data[3].line.dash == "dot"
    assert league_figure.data[2].line.dash == "dash"
    assert league_figure.data[3].line.color != league_figure.data[2].line.color


def test_the_mlb_trace_has_no_hover(league_figure) -> None:
    assert league_figure.data[3].hoverinfo == "skip"


def test_mlb_context_does_not_change_the_existing_series(league_figure) -> None:
    """The rolling average and the game values mean exactly what they did."""
    assert list(league_figure.data[0].y) == VALUES
    assert list(league_figure.data[1].y) == pytest.approx([10.0, 7.0, 28 / 3, 8.5, 9.0])
    assert list(league_figure.data[2].y) == pytest.approx([9.0, 9.0])


def test_no_trace_uses_spline_smoothing_with_mlb_context(league_figure) -> None:
    assert all(trace.line.shape in (None, "linear") for trace in league_figure.data)


def test_only_the_mlb_line_is_labelled_when_it_is_drawn(league_figure) -> None:
    """Both lines can sit a tenth of a strikeout apart, where labels collide."""
    annotations = league_figure.layout.annotations
    assert len(annotations) == 1
    assert MLB_AVERAGE_TRACE_NAME in annotations[0].text
    assert "8.20" in annotations[0].text


def test_raw_trace_is_labelled_as_strikeouts(figure) -> None:
    assert figure.data[0].name == "Game Strikeouts"


@pytest.mark.parametrize("window", [5, 10, 15, 30])
def test_rolling_trace_label_reflects_the_selected_window(window: int) -> None:
    figure = build_team_strikeouts_figure(analysis_for([7] * 40, window))
    assert figure.data[1].name == f"{window}-Game Average"
    assert rolling_average_trace_name(window) == f"{window}-Game Average"


def test_raw_trace_plots_the_game_strikeouts(figure) -> None:
    assert list(figure.data[0].y) == VALUES


def test_raw_trace_uses_the_season_game_number_for_x(figure) -> None:
    assert list(figure.data[0].x) == [1, 2, 3, 4, 5]


def test_rolling_trace_plots_the_trailing_average(figure) -> None:
    assert list(figure.data[1].y) == pytest.approx([10.0, 7.0, 28 / 3, 8.5, 9.0])


def test_rolling_trace_joins_points_with_straight_segments(figure) -> None:
    """Splines would draw averages between games that were never calculated."""
    assert figure.data[1].line.shape in (None, "linear")


def test_no_trace_uses_spline_smoothing(figure) -> None:
    assert all(trace.line.shape in (None, "linear") for trace in figure.data)


def test_the_team_line_says_whose_average_it_is(figure) -> None:
    """Two reference lines can share the chart, so "Season Average" is ambiguous."""
    assert figure.data[2].name == "Team Season Average"


def test_season_average_trace_is_the_stored_season_average(figure) -> None:
    expected = sum(VALUES) / len(VALUES)
    assert list(figure.data[2].y) == pytest.approx([expected, expected])


def test_season_average_trace_spans_the_whole_season(figure) -> None:
    assert list(figure.data[2].x) == [1, 5]


def test_the_season_average_line_is_labelled_with_its_value(figure) -> None:
    annotation = figure.layout.annotations[0]
    assert TEAM_SEASON_AVERAGE_TRACE_NAME in annotation.text
    assert "9.00" in annotation.text
    assert MLB_AVERAGE_TRACE_NAME not in annotation.text


def test_x_axis_ticks_carry_the_game_date(figure) -> None:
    """A game number alone does not say when in the season a stretch happened."""
    assert figure.layout.xaxis.ticktext[0] == "1<br>Mar 27"


def test_season_average_trace_is_dashed(figure) -> None:
    assert figure.data[2].line.dash == "dash"


def test_season_average_trace_reads_the_authoritative_summary_value() -> None:
    analysis = analysis_for(VALUES)
    figure = build_team_strikeouts_figure(analysis)
    assert figure.data[2].y[0] == pytest.approx(analysis.summary.season_average)


def test_axis_titles_name_batting_strikeouts(figure) -> None:
    assert figure.layout.xaxis.title.text == X_AXIS_TITLE
    assert figure.layout.yaxis.title.text == STRIKEOUTS_Y_AXIS_TITLE


def test_y_axis_says_batting_so_pitching_cannot_be_assumed(figure) -> None:
    assert figure.layout.yaxis.title.text == "Batting Strikeouts per Game"


def test_y_axis_has_no_hardcoded_maximum(figure) -> None:
    assert figure.layout.yaxis.range is None


def test_y_axis_grows_with_an_unusually_high_game() -> None:
    figure = build_team_strikeouts_figure(analysis_for([2, 2, 24]))
    assert figure.layout.yaxis.range is None


def test_y_axis_starts_at_zero() -> None:
    assert (
        build_team_strikeouts_figure(analysis_for(VALUES)).layout.yaxis.rangemode
        == "tozero"
    )


def test_hover_shows_date_matchup_strikeouts_and_average(figure) -> None:
    template = figure.data[0].hovertemplate
    assert "Batting Strikeouts: %{customdata[2]}" in template
    assert "5-Game Avg: %{customdata[3]:.2f}" in template


def test_hover_data_carries_formatted_date_and_matchup(figure) -> None:
    first = figure.data[0].customdata[0]
    assert first[0] == "March 27, 2025"
    assert first[1].startswith("vs ")
    assert first[2] == VALUES[0]


def test_hover_matchup_uses_at_for_away_games(figure) -> None:
    assert figure.data[0].customdata[1][1].startswith("at ")


def test_season_average_trace_has_no_hover(figure) -> None:
    assert figure.data[2].hoverinfo == "skip"


def test_rendered_html_uses_the_strikeout_div_id(figure) -> None:
    html = render_figure_html(figure, div_id=STRIKEOUTS_CHART_DIV_ID)
    assert STRIKEOUTS_CHART_DIV_ID in html
    assert "team-hits-chart" not in html
