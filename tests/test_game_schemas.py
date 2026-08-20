"""Tests for the normalized game schemas."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.games import TeamGameBattingLine

VALID_LINE = {
    "game_pk": 776704,
    "game_date": date(2025, 8, 17),
    "season": 2025,
    "team_id": 112,
    "team_name": "Chicago Cubs",
    "opponent_id": 134,
    "opponent_name": "Pittsburgh Pirates",
    "home_away": "home",
    "hits": 6,
    "runs": 4,
    "status": "Final",
    "game_number": 1,
    "doubleheader": False,
    "scheduled_innings": 9,
}


def test_valid_line_is_accepted() -> None:
    line = TeamGameBattingLine(**VALID_LINE)
    assert line.game_date == date(2025, 8, 17)
    assert line.home_away == "home"


def test_game_date_accepts_iso_string_as_date() -> None:
    line = TeamGameBattingLine(**{**VALID_LINE, "game_date": "2025-08-17"})
    assert line.game_date == date(2025, 8, 17)


@pytest.mark.parametrize("field", ["hits", "runs"])
def test_negative_hitting_values_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        TeamGameBattingLine(**{**VALID_LINE, field: -1})


@pytest.mark.parametrize("value", ["Home", "HOME", "neutral", "", "h"])
def test_invalid_home_away_values_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        TeamGameBattingLine(**{**VALID_LINE, "home_away": value})


@pytest.mark.parametrize("field", ["game_pk", "season", "team_id", "opponent_id"])
def test_non_positive_ids_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        TeamGameBattingLine(**{**VALID_LINE, field: 0})


@pytest.mark.parametrize("field", ["team_name", "opponent_name", "status"])
def test_empty_display_values_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        TeamGameBattingLine(**{**VALID_LINE, field: ""})


def test_game_number_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        TeamGameBattingLine(**{**VALID_LINE, "game_number": 0})


def test_scheduled_innings_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        TeamGameBattingLine(**{**VALID_LINE, "scheduled_innings": 0})


def test_seven_inning_games_are_allowed() -> None:
    line = TeamGameBattingLine(**{**VALID_LINE, "scheduled_innings": 7})
    assert line.scheduled_innings == 7


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TeamGameBattingLine(**{**VALID_LINE, "raw_response": {"gamePk": 776704}})


def test_json_serialization_uses_iso_dates() -> None:
    line = TeamGameBattingLine(**VALID_LINE)
    assert line.model_dump(mode="json")["game_date"] == "2025-08-17"


def test_strikeouts_are_accepted_when_present() -> None:
    line = TeamGameBattingLine(**{**VALID_LINE, "strikeouts": 9})
    assert line.strikeouts == 9


def test_zero_strikeouts_is_a_real_value() -> None:
    """A game in which nobody struck out is legitimate, unlike a missing total."""
    assert TeamGameBattingLine(**{**VALID_LINE, "strikeouts": 0}).strikeouts == 0


def test_strikeouts_default_to_none_for_rows_predating_collection() -> None:
    """Rows persisted before Milestone 3.5 carry an unknown, not a zero."""
    assert TeamGameBattingLine(**VALID_LINE).strikeouts is None


def test_explicit_none_strikeouts_is_accepted() -> None:
    assert TeamGameBattingLine(**{**VALID_LINE, "strikeouts": None}).strikeouts is None


def test_negative_strikeouts_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TeamGameBattingLine(**{**VALID_LINE, "strikeouts": -1})
