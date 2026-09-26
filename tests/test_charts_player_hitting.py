"""The Player plate-appearance rate profile figure contract."""

import plotly.graph_objects as go
import pytest

from app.analytics.player_hitting import build_player_hitting_overview
from app.web.charts import (
    PLAYER_PA_RATES_X_AXIS_TITLE,
    build_player_plate_appearance_rates_figure,
)
from tests.test_repositories_players import make_hitting


def _figure() -> go.Figure:
    rates = build_player_hitting_overview(make_hitting()).plate_appearance_rates
    assert rates is not None
    return build_player_plate_appearance_rates_figure(rates)


def test_three_bars_share_the_plate_appearance_denominator() -> None:
    figure = _figure()
    assert len(figure.data) == 1
    bars = figure.data[0]
    assert bars.type == "bar"
    assert list(bars.y) == ["K%", "BB%", "HR%"]
    assert list(bars.x) == pytest.approx([100 / 600, 60 / 600, 20 / 600])
    assert list(bars.text) == ["16.7%", "10.0%", "3.3%"]
    assert figure.layout.xaxis.title.text == PLAYER_PA_RATES_X_AXIS_TITLE
    assert figure.layout.xaxis.tickformat == ".0%"


def test_no_quality_colouring_or_reference_lines() -> None:
    figure = _figure()
    # One colour for every bar: a strikeout is not styled as worse than a walk.
    assert isinstance(figure.data[0].marker.color, str)
    assert not figure.layout.shapes
    assert not figure.layout.annotations
    assert figure.layout.xaxis.range[0] == 0
