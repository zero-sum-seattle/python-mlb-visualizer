"""Tests for the run differential presentation helpers.

Signs carry meaning on this page in a way they do not elsewhere, so most of
these assert that a sign is present and correct rather than that a number is.
"""

import pytest

from app.analytics.team_run_differential import build_team_run_differential_analysis
from app.web.formatting import (
    build_run_differential_summary_cards,
    format_missing_opponent_note,
    format_pythagorean_note,
    format_win_pct,
)
from tests.factories import make_run_result_season


def analysis_for(scored, allowed, window: int = 5):
    return build_team_run_differential_analysis(
        make_run_result_season(scored, allowed), rolling_window=window
    )


class TestWinPct:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.5, ".500"),
            (0.512, ".512"),
            (0.0, ".000"),
            (1.0, "1.000"),
            (0.6215, ".622"),
        ],
    )
    def test_it_is_written_the_way_baseball_writes_it(
        self, value: float, expected: str
    ) -> None:
        """Leading zero dropped, three decimals, except for a perfect 1.000."""
        assert format_win_pct(value) == expected


class TestSummaryCards:
    def test_there_are_four_cards(self) -> None:
        cards = build_run_differential_summary_cards(analysis_for([6, 2], [3, 7]))

        assert len(cards) == 4

    def test_a_positive_differential_carries_a_plus(self) -> None:
        cards = build_run_differential_summary_cards(analysis_for([6, 8], [3, 1]))

        season = next(card for card in cards if card.label == "Season Run Differential")
        assert season.value == "+10"

    def test_a_negative_differential_carries_a_minus(self) -> None:
        cards = build_run_differential_summary_cards(analysis_for([1, 2], [5, 8]))

        season = next(card for card in cards if card.label == "Season Run Differential")
        assert season.value == "-10"

    def test_a_dead_even_season_still_shows_a_sign(self) -> None:
        """+0 is a real result and must not be mistaken for a missing value."""
        cards = build_run_differential_summary_cards(analysis_for([5, 1], [1, 5]))

        season = next(card for card in cards if card.label == "Season Run Differential")
        assert season.value == "+0"

    def test_the_caption_names_both_run_totals(self) -> None:
        cards = build_run_differential_summary_cards(analysis_for([6, 8], [3, 1]))

        season = next(card for card in cards if card.label == "Season Run Differential")
        assert season.caption == "14 Scored, 4 Allowed"

    def test_the_actual_record_card_shows_wins_losses_and_the_gap(self) -> None:
        cards = build_run_differential_summary_cards(
            analysis_for([5, 1, 3, 9], [2, 4, 8, 0], window=4)
        )

        actual = next(card for card in cards if card.label == "Actual Record")
        assert actual.value == "2-2"
        assert ".500" in actual.caption
        assert "vs Expected" in actual.caption

    def test_the_recent_average_carries_a_sign(self) -> None:
        cards = build_run_differential_summary_cards(analysis_for([1, 2], [5, 8]))

        recent = next(card for card in cards if "Recent" in card.label)
        assert recent.value.startswith("-")

    def test_there_is_no_vs_mlb_card(self) -> None:
        """League-wide run differential is zero, so the slot holds the expectation."""
        cards = build_run_differential_summary_cards(analysis_for([6, 2], [3, 7]))

        assert "vs MLB" not in [card.label for card in cards]
        assert "Pythagorean Record" in [card.label for card in cards]


class TestPythagoreanNote:
    def test_a_team_matching_its_expectation_is_described_as_such(self) -> None:
        """Alternating 4-3 wins and 3-4 losses: even runs, even record, no gap."""
        note = format_pythagorean_note(
            analysis_for([4, 3, 4, 3], [3, 4, 3, 4], window=4)
        )

        assert "within a game" in note

    def test_outperforming_the_expectation_is_explained(self) -> None:
        note = format_pythagorean_note(
            analysis_for([2, 2, 2, 1], [1, 1, 1, 12], window=4)
        )

        assert "above" in note
        assert "close games won and blowouts lost" in note

    def test_underperforming_the_expectation_is_explained(self) -> None:
        note = format_pythagorean_note(
            analysis_for([1, 1, 1, 12], [2, 2, 2, 1], window=4)
        )

        assert "below" in note
        assert "close games lost and blowouts won" in note

    def test_it_names_the_exponent_so_the_figure_can_be_checked(self) -> None:
        note = format_pythagorean_note(analysis_for([6, 2], [3, 7]))

        assert "1.83" in note

    def test_it_does_not_present_itself_as_a_forecast(self) -> None:
        note = format_pythagorean_note(
            analysis_for([2, 2, 2, 1], [1, 1, 1, 12], window=4)
        )

        assert "describes games already played" in note


class TestMissingOpponentNote:
    def test_it_names_the_league_import_not_a_team_reimport(self) -> None:
        note = format_missing_opponent_note(
            season=2025,
            missing_game_count=12,
            total_games=100,
            league_import_command=(
                "poetry run python scripts/import_league_season.py --season 2025"
            ),
        )

        assert "import_league_season.py" in note
        assert "import_team_season.py" not in note

    def test_it_explains_where_runs_allowed_comes_from(self) -> None:
        note = format_missing_opponent_note(
            season=2025,
            missing_game_count=12,
            total_games=100,
            league_import_command="cmd",
        )

        assert "opponent's own record" in note

    def test_one_missing_game_reads_in_the_singular(self) -> None:
        note = format_missing_opponent_note(
            season=2025,
            missing_game_count=1,
            total_games=100,
            league_import_command="cmd",
        )

        assert "1 of the 100 2025 game stored for this team has" in note
        assert "unknown for it" in note

    def test_several_missing_games_read_in_the_plural(self) -> None:
        note = format_missing_opponent_note(
            season=2025,
            missing_game_count=12,
            total_games=100,
            league_import_command="cmd",
        )

        assert "12 of the 100 2025 games stored for this team have" in note
        assert "unknown for them" in note

    def test_large_counts_are_grouped(self) -> None:
        note = format_missing_opponent_note(
            season=2025,
            missing_game_count=1620,
            total_games=4860,
            league_import_command="cmd",
        )

        assert "1,620 of the 4,860" in note
