"""Tests for presentation formatting helpers."""

from datetime import date

import pytest

from app.analytics.team_hitting import build_team_hits_analysis
from app.analytics.team_strikeouts import build_team_strikeouts_analysis
from app.web.formatting import (
    build_strikeout_summary_cards,
    build_summary_cards,
    format_long_date,
    format_matchup,
)
from tests.factories import make_season


def test_long_date_has_no_padded_day() -> None:
    assert format_long_date(date(2025, 5, 8)) == "May 8, 2025"


def test_long_date_spells_out_the_month() -> None:
    assert format_long_date(date(2025, 9, 28)) == "September 28, 2025"


def test_matchup_uses_vs_at_home_and_at_on_the_road() -> None:
    assert format_matchup("Minnesota Twins", "home") == "vs Minnesota Twins"
    assert format_matchup("Minnesota Twins", "away") == "at Minnesota Twins"


def test_summary_cards_are_labelled_with_the_selected_window() -> None:
    analysis = build_team_hits_analysis(make_season([8] * 40), rolling_window=10)
    labels = [card.label for card in build_summary_cards(analysis)]
    assert labels == [
        "Recent 10-Game Avg",
        "Season Avg",
        "vs Prior 10",
        "Games Played",
    ]


def test_summary_card_values_are_rounded_for_display() -> None:
    analysis = build_team_hits_analysis(
        make_season([4] * 5 + [7] * 5), rolling_window=5
    )
    cards = build_summary_cards(analysis)
    assert cards[0].value == "7.00"
    assert cards[1].value == "5.50"
    assert cards[3].value == "10"


def test_change_card_is_signed() -> None:
    analysis = build_team_hits_analysis(
        make_season([4] * 5 + [7] * 5), rolling_window=5
    )
    assert build_summary_cards(analysis)[2].value == "+3.00"

    declining = build_team_hits_analysis(
        make_season([9] * 5 + [8] * 5), rolling_window=5
    )
    assert build_summary_cards(declining)[2].value == "-1.00"


def test_change_card_explains_a_missing_prior_window() -> None:
    analysis = build_team_hits_analysis(make_season([6] * 9), rolling_window=5)
    card = build_summary_cards(analysis)[2]
    assert card.value == "—"
    assert card.caption == "Not enough games"


def test_hits_per_game_is_the_caption_for_rate_cards() -> None:
    analysis = build_team_hits_analysis(make_season([6] * 20), rolling_window=5)
    cards = build_summary_cards(analysis)
    assert cards[0].caption == "Hits per Game"
    assert cards[3].caption == "Completed Games"


def test_strikeout_cards_describe_batting_strikeouts() -> None:
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 4, strikeouts=[10, 8, 12, 6]), rolling_window=2
    )
    cards = build_strikeout_summary_cards(analysis)
    assert [card.label for card in cards] == [
        "Recent 2-Game Avg",
        "Season Avg",
        "vs Prior 2",
        "Games Played",
    ]
    assert cards[0].caption == "Batting Strikeouts per Game"


def test_strikeout_cards_round_for_display_only() -> None:
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 3, strikeouts=[1, 1, 0]), rolling_window=3
    )
    cards = build_strikeout_summary_cards(analysis)
    assert cards[1].value == "0.67"
    assert analysis.summary.season_average == pytest.approx(2 / 3)


def test_strikeout_change_card_is_signed_and_neutral() -> None:
    """A plain signed number: no wording that calls a direction good or bad."""
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 4, strikeouts=[2, 2, 8, 8]), rolling_window=2
    )
    change = build_strikeout_summary_cards(analysis)[2]
    assert change.value == "+6.00"
    assert change.caption == "Batting Strikeouts per Game"


def test_strikeout_decrease_is_shown_as_a_negative_number() -> None:
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 4, strikeouts=[8, 8, 2, 2]), rolling_window=2
    )
    assert build_strikeout_summary_cards(analysis)[2].value == "-6.00"


def test_strikeout_change_card_says_when_there_are_too_few_games() -> None:
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 3, strikeouts=[5, 5, 5]), rolling_window=3
    )
    change = build_strikeout_summary_cards(analysis)[2]
    assert change.value == "—"
    assert change.caption == "Not enough games"


def test_strikeout_games_played_counts_completed_games() -> None:
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 5, strikeouts=[5] * 5), rolling_window=2
    )
    games = build_strikeout_summary_cards(analysis)[3]
    assert (games.value, games.caption) == ("5", "Completed Games")
