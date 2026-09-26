"""Presentation of Player season hitting rates and totals."""

import pytest

from app.analytics.player_hitting import build_player_hitting_overview
from app.web.formatting import (
    UNDEFINED_RATE_VALUE,
    build_player_hitting_rate_cards,
    build_player_hitting_total_cards,
    format_batting_rate,
    format_plate_appearance_rate,
)
from tests.test_analytics_player_hitting import NO_PLATE_APPEARANCES
from tests.test_repositories_players import make_hitting


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.3, ".300"),
        (215 / 569, ".378"),
        (0.0, ".000"),
        (1.0243, "1.024"),
        (0.9996, "1.000"),
    ],
)
def test_batting_rates_use_box_score_notation(value: float, expected: str) -> None:
    assert format_batting_rate(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(100 / 600, "16.7%"), (0.1, "10.0%"), (0.0, "0.0%")]
)
def test_plate_appearance_rates_are_percentages(value: float, expected: str) -> None:
    assert format_plate_appearance_rate(value) == expected


def test_rate_cards_round_only_for_display() -> None:
    overview = build_player_hitting_overview(make_hitting())
    cards = build_player_hitting_rate_cards(overview)
    assert [(card.label, card.value) for card in cards] == [
        ("AVG", ".300"),
        ("OBP", ".378"),
        ("SLG", ".492"),
        ("OPS", ".870"),
    ]
    assert cards[0].caption == "150 H in 500 AB"
    assert cards[2].caption == "246 TB in 500 AB"


def test_undefined_rates_render_as_explained_dashes_not_zero() -> None:
    overview = build_player_hitting_overview(make_hitting(**NO_PLATE_APPEARANCES))
    cards = build_player_hitting_rate_cards(overview)
    assert {card.value for card in cards} == {UNDEFINED_RATE_VALUE}
    assert all(card.caption.startswith("Undefined:") for card in cards)


def test_total_cards_show_stored_counts() -> None:
    overview = build_player_hitting_overview(
        make_hitting(plate_appearances=1_200, at_bats=1_050)
    )
    cards = build_player_hitting_total_cards(overview)
    assert [(card.label, card.value) for card in cards] == [
        ("Games", "150"),
        ("Plate Appearances", "1,200"),
        ("Home Runs", "20"),
        ("Walks", "60"),
        ("Strikeouts", "100"),
        ("Stolen Bases", "10"),
    ]
    assert cards[3].caption == "Includes 5 intentional"
    assert cards[5].caption == "3 caught stealing"
