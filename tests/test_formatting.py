"""Tests for presentation formatting helpers."""

from datetime import date

import pytest

from app.analytics.league_hitting import compare_team_hits_to_league
from app.analytics.team_hitting import build_team_hits_analysis
from app.analytics.team_strikeouts import build_team_strikeouts_analysis
from app.web.formatting import (
    LEAGUE_COMPARISON_UNAVAILABLE_NOTE,
    build_strikeout_summary_cards,
    build_summary_cards,
    format_league_comparison_note,
    format_long_date,
    format_matchup,
)
from tests.factories import make_league_hits_context, make_season


def test_long_date_has_no_padded_day() -> None:
    assert format_long_date(date(2025, 5, 8)) == "May 8, 2025"


def test_long_date_spells_out_the_month() -> None:
    assert format_long_date(date(2025, 9, 28)) == "September 28, 2025"


def test_matchup_uses_vs_at_home_and_at_on_the_road() -> None:
    assert format_matchup("Minnesota Twins", "home") == "vs Minnesota Twins"
    assert format_matchup("Minnesota Twins", "away") == "at Minnesota Twins"


def comparison(hits: list[int], *, window: int, mlb_hits_per_game: float):
    """Build a team analysis and an MLB comparison against a chosen average."""
    analysis = build_team_hits_analysis(make_season(hits), rolling_window=window)
    league = make_league_hits_context(
        total_hits=round(mlb_hits_per_game * 100), team_game_records=100
    )
    return analysis, compare_team_hits_to_league(analysis, league)


def test_summary_cards_are_labelled_with_the_selected_window() -> None:
    """Milestone 5 replaced the prior-window card with the MLB comparison.

    ``TeamHitsSummary`` still calculates the prior-window change, and the
    strikeouts page still shows it; only the hits card row changed, so that the
    row keeps four cards instead of growing a fifth.
    """
    analysis = build_team_hits_analysis(make_season([8] * 40), rolling_window=10)
    labels = [card.label for card in build_summary_cards(analysis)]
    assert labels == [
        "Recent 10-Game Avg",
        "Season Avg",
        "vs MLB",
        "Games Played",
    ]
    assert analysis.summary.change_vs_prior_window is not None


def test_summary_card_values_are_rounded_for_display() -> None:
    analysis = build_team_hits_analysis(
        make_season([4] * 5 + [7] * 5), rolling_window=5
    )
    cards = build_summary_cards(analysis)
    assert cards[0].value == "7.00"
    assert cards[1].value == "5.50"
    assert cards[3].value == "10"


def test_league_card_is_signed() -> None:
    above, above_comparison = comparison([9] * 10, window=5, mlb_hits_per_game=8.50)
    card = build_summary_cards(above, above_comparison)[2]
    assert (card.value, card.caption) == ("+0.50", "Hits per Game")

    below, below_comparison = comparison([8] * 10, window=5, mlb_hits_per_game=8.25)
    assert build_summary_cards(below, below_comparison)[2].value == "-0.25"


def test_league_card_says_when_no_mlb_average_is_available() -> None:
    """Without complete league coverage the card must not invent a number."""
    analysis = build_team_hits_analysis(make_season([6] * 9), rolling_window=5)
    card = build_summary_cards(analysis)[2]
    assert card.value == "—"
    assert card.caption == "Comparison unavailable"


def test_league_note_explains_why_a_comparison_is_missing() -> None:
    note = format_league_comparison_note(None)
    assert note == LEAGUE_COMPARISON_UNAVAILABLE_NOTE
    assert "complete league-season import" in note


def test_league_note_reports_the_average_and_what_it_covers() -> None:
    _, available = comparison([9] * 10, window=5, mlb_hits_per_game=8.20)
    note = format_league_comparison_note(available)
    assert "8.20 hits per game" in note
    assert "100 team-game records" in note
    assert "currently stored" in note
    assert "finished being played" in note


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
