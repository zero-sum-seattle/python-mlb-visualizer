"""Tests for presentation formatting helpers."""

from datetime import date

import pytest

from app.analytics.league_hitting import compare_team_hits_to_league
from app.analytics.league_runs import compare_team_runs_to_league
from app.analytics.league_strikeouts import compare_team_strikeouts_to_league
from app.analytics.team_hitting import build_team_hits_analysis
from app.analytics.team_hitting_comparison import (
    build_team_hitting_comparison_analysis,
)
from app.analytics.team_runs import build_team_runs_analysis
from app.analytics.team_strikeouts import build_team_strikeouts_analysis
from app.web.formatting import (
    LEAGUE_COMPARISON_UNAVAILABLE_NOTE,
    LEAGUE_RUNS_UNAVAILABLE_NOTE,
    LEAGUE_STRIKEOUTS_UNAVAILABLE_NOTE,
    build_hitting_comparison_summary_cards,
    build_runs_summary_cards,
    build_strikeout_summary_cards,
    build_summary_cards,
    format_league_comparison_note,
    format_league_runs_note,
    format_league_strikeouts_backfill_note,
    format_league_strikeouts_note,
    format_long_date,
    format_matchup,
    format_short_date,
)
from tests.factories import (
    make_league_hits_context,
    make_league_runs_context,
    make_league_strikeouts_context,
    make_season,
)


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


def strikeout_comparison(
    strikeouts: list[int], *, window: int, mlb_strikeouts_per_game: float
):
    """Build a strikeout analysis and an MLB comparison against a chosen average."""
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * len(strikeouts), strikeouts=strikeouts),
        rolling_window=window,
    )
    league = make_league_strikeouts_context(
        total_strikeouts=round(mlb_strikeouts_per_game * 100), team_game_records=100
    )
    return analysis, compare_team_strikeouts_to_league(analysis, league)


def test_strikeout_cards_describe_batting_strikeouts() -> None:
    """Issue #23 replaced the prior-window card with the MLB comparison.

    ``TeamStrikeoutsSummary`` still calculates the prior-window change and
    ``tests/test_analytics_team_strikeouts.py`` still covers it; only the card
    row changed, so that the row keeps four cards instead of growing a fifth.
    """
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 4, strikeouts=[10, 8, 12, 6]), rolling_window=2
    )
    cards = build_strikeout_summary_cards(analysis)
    assert [card.label for card in cards] == [
        "Recent 2-Game Avg",
        "Season Avg",
        "vs MLB",
        "Games Played",
    ]
    assert cards[0].caption == "Batting Strikeouts per Game"
    assert analysis.summary.change_vs_prior_window is not None


def test_strikeout_cards_round_for_display_only() -> None:
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 3, strikeouts=[1, 1, 0]), rolling_window=3
    )
    cards = build_strikeout_summary_cards(analysis)
    assert cards[1].value == "0.67"
    assert analysis.summary.season_average == pytest.approx(2 / 3)


def test_striking_out_more_than_mlb_reads_as_a_positive_number() -> None:
    """A plain signed number: no wording that calls a direction good or bad."""
    analysis, comparison = strikeout_comparison(
        [9] * 10, window=5, mlb_strikeouts_per_game=8.40
    )
    card = build_strikeout_summary_cards(analysis, comparison)[2]
    assert (card.value, card.caption) == ("+0.60", "Batting Strikeouts per Game")


def test_striking_out_less_than_mlb_reads_as_a_negative_number() -> None:
    """The worked example from the issue: 7.80 against 8.40 shows -0.60."""
    analysis, comparison = strikeout_comparison(
        [8, 8, 8, 8, 7], window=5, mlb_strikeouts_per_game=8.40
    )
    card = build_strikeout_summary_cards(analysis, comparison)[2]
    assert card.value == "-0.60"


def test_strikeout_league_card_says_when_no_mlb_average_is_available() -> None:
    """Without trustworthy league data the card must not invent a number."""
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 9, strikeouts=[6] * 9), rolling_window=5
    )
    card = build_strikeout_summary_cards(analysis)[2]
    assert card.value == "—"
    assert card.caption == "Comparison unavailable"
    assert "0.00" not in card.value


def test_strikeout_league_note_explains_why_a_comparison_is_missing() -> None:
    note = format_league_strikeouts_note(None)
    assert note == LEAGUE_STRIKEOUTS_UNAVAILABLE_NOTE
    assert "complete league-season import" in note
    assert "batting strikeout" in note


def test_strikeout_league_note_reports_the_average_and_what_it_covers() -> None:
    _, comparison = strikeout_comparison(
        [9] * 10, window=5, mlb_strikeouts_per_game=8.40
    )
    note = format_league_strikeouts_note(comparison)
    assert "8.40 times per game" in note
    assert "100 team-game records" in note
    assert "currently stored" in note
    assert "finished being played" in note


def test_strikeout_league_note_says_batting_not_pitching() -> None:
    """A per-game strikeout number must not be readable as the team's pitching."""
    _, comparison = strikeout_comparison(
        [9] * 10, window=5, mlb_strikeouts_per_game=8.40
    )
    note = format_league_strikeouts_note(comparison)
    assert "MLB hitters struck out" in note
    assert "total batting strikeouts divided by total team-game records" in note


def test_strikeout_league_note_does_not_call_the_season_finished() -> None:
    _, comparison = strikeout_comparison(
        [9] * 10, window=5, mlb_strikeouts_per_game=8.40
    )
    assert "season complete" not in format_league_strikeouts_note(comparison).lower()


def test_backfill_note_names_the_gap_and_the_league_import() -> None:
    """Complete coverage plus legacy nulls is a different problem, said plainly."""
    note = format_league_strikeouts_backfill_note(
        season=2025,
        records_missing=12,
        records_total=4860,
        reimport_command=(
            "poetry run python scripts/import_league_season.py --season 2025"
        ),
    )
    assert "12 of the 4,860 team-game records stored for 2025 have no" in note
    assert "not counted as zero" in note
    assert "import_league_season.py --season 2025" in note
    assert note != LEAGUE_STRIKEOUTS_UNAVAILABLE_NOTE


def test_backfill_note_reads_correctly_for_a_single_missing_record() -> None:
    """One unknown total disqualifies the season, so the sentence must fit it."""
    note = format_league_strikeouts_backfill_note(
        season=2026,
        records_missing=1,
        records_total=4860,
        reimport_command="poetry run python scripts/import_league_season.py",
    )
    assert "1 of the 4,860 team-game records stored for 2026 has no" in note


def test_strikeout_games_played_counts_completed_games() -> None:
    analysis = build_team_strikeouts_analysis(
        make_season(hits=[8] * 5, strikeouts=[5] * 5), rolling_window=2
    )
    games = build_strikeout_summary_cards(analysis)[3]
    assert (games.value, games.caption) == ("5", "Completed Games")


def test_short_date_drops_the_year_for_axis_ticks() -> None:
    assert format_short_date(date(2025, 5, 8)) == "May 8"
    assert format_short_date(date(2025, 9, 28)) == "Sep 28"


def test_normalized_comparison_cards_use_the_requested_labels_and_values() -> None:
    games = make_season(
        hits=[8] * 5,
        strikeouts=[9] * 5,
    )
    hits = build_team_hits_analysis(games, rolling_window=5)
    strikeouts = build_team_strikeouts_analysis(games, rolling_window=5)
    analysis = build_team_hitting_comparison_analysis(
        hits,
        strikeouts,
        make_league_hits_context(total_hits=800, team_game_records=100),
        make_league_strikeouts_context(total_strikeouts=1000, team_game_records=100),
    )

    cards = build_hitting_comparison_summary_cards(analysis)
    assert [card.label for card in cards] == [
        "Recent Hits Index",
        "Recent K Index",
        "Trend Gap",
        "Games Played",
    ]
    assert [card.value for card in cards] == ["100", "90", "+10", "5"]
    assert cards[0].caption == "MLB Avg = 100"
    assert cards[1].caption == "MLB Avg = 100"
    assert cards[2].caption == "Hits Index − K Index"
    assert cards[3].caption == "Completed Games"


def test_normalized_comparison_cards_keep_one_meaningful_decimal() -> None:
    games = make_season(hits=[9] * 5, strikeouts=[9] * 5)
    analysis = build_team_hitting_comparison_analysis(
        build_team_hits_analysis(games, rolling_window=5),
        build_team_strikeouts_analysis(games, rolling_window=5),
        make_league_hits_context(total_hits=800, team_game_records=100),
        make_league_strikeouts_context(total_strikeouts=1000, team_game_records=100),
    )

    cards = build_hitting_comparison_summary_cards(analysis)
    assert [card.value for card in cards[:3]] == ["112.5", "90", "+22.5"]


def runs_comparison(runs: list[int], *, window: int, mlb_runs_per_game: float):
    """Build a team runs analysis and an MLB comparison against a chosen average."""
    analysis = build_team_runs_analysis(
        make_season(hits=[8] * len(runs), runs=runs), rolling_window=window
    )
    league = make_league_runs_context(
        total_runs=round(mlb_runs_per_game * 100), team_game_records=100
    )
    return analysis, compare_team_runs_to_league(analysis, league)


def test_runs_cards_use_the_same_four_labels_as_the_other_pages() -> None:
    analysis, comparison = runs_comparison([4] * 40, window=10, mlb_runs_per_game=4.42)
    cards = build_runs_summary_cards(analysis, comparison)
    assert [card.label for card in cards] == [
        "Recent 10-Game Avg",
        "Season Avg",
        "vs MLB",
        "Games Played",
    ]


def test_runs_cards_are_captioned_as_runs_scored() -> None:
    analysis, comparison = runs_comparison([4] * 20, window=5, mlb_runs_per_game=4.0)
    cards = build_runs_summary_cards(analysis, comparison)
    assert cards[0].caption == "Runs Scored per Game"
    assert cards[1].caption == "Runs Scored per Game"
    assert cards[2].caption == "Runs Scored per Game"
    assert cards[3].caption == "Completed Games"


def test_runs_cards_round_for_display_only() -> None:
    analysis = build_team_runs_analysis(
        make_season(hits=[8] * 10, runs=[3] * 5 + [6] * 5), rolling_window=5
    )
    cards = build_runs_summary_cards(analysis)
    assert cards[0].value == "6.00"
    assert cards[1].value == "4.50"
    assert cards[3].value == "10"


def test_scoring_more_than_mlb_reads_as_a_positive_number() -> None:
    """The worked example from issue #24: 4.75 against 4.42 reads +0.33."""
    analysis, comparison = runs_comparison(
        [5, 5, 5, 4], window=4, mlb_runs_per_game=4.42
    )
    card = build_runs_summary_cards(analysis, comparison)[2]
    assert card.value == "+0.33"


def test_scoring_less_than_mlb_reads_as_a_negative_number() -> None:
    analysis, comparison = runs_comparison([4] * 4, window=4, mlb_runs_per_game=4.42)
    assert build_runs_summary_cards(analysis, comparison)[2].value == "-0.42"


def test_matching_mlb_exactly_reads_as_a_signed_zero() -> None:
    """+0.00 is a real result and must stay distinct from unavailable."""
    analysis, comparison = runs_comparison([4] * 4, window=4, mlb_runs_per_game=4.0)
    assert build_runs_summary_cards(analysis, comparison)[2].value == "+0.00"


def test_runs_league_card_says_when_no_mlb_average_is_available() -> None:
    """Without complete league coverage the card must not invent a number."""
    analysis = build_team_runs_analysis(
        make_season(hits=[8] * 9, runs=[4] * 9), rolling_window=5
    )
    card = build_runs_summary_cards(analysis)[2]
    assert card.value == "—"
    assert card.caption == "Comparison unavailable"
    assert card.value != "0.00"


def test_runs_league_note_explains_why_a_comparison_is_missing() -> None:
    note = format_league_runs_note(None)
    assert note == LEAGUE_RUNS_UNAVAILABLE_NOTE
    assert "complete league-season import" in note


def test_runs_league_note_reports_the_average_and_what_it_covers() -> None:
    _, available = runs_comparison([5] * 10, window=5, mlb_runs_per_game=4.42)
    note = format_league_runs_note(available)
    assert "scored 4.42 runs per game" in note
    assert "100 team-game records" in note
    assert "currently stored" in note
    assert "total runs divided by total team-game records" in note


def test_runs_league_note_does_not_call_the_season_finished() -> None:
    _, available = runs_comparison([5] * 10, window=5, mlb_runs_per_game=4.42)
    note = format_league_runs_note(available)
    assert "finished being played" in note
    assert "season complete" not in note.lower()


def test_runs_games_played_counts_completed_games() -> None:
    analysis = build_team_runs_analysis(
        make_season(hits=[8] * 12, runs=[4] * 12), rolling_window=5
    )
    assert build_runs_summary_cards(analysis)[3].value == "12"
