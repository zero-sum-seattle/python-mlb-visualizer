"""Tests for player identity and player-season hitting domain schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.players import PlayerIdentity, PlayerSeasonHitting

PLAYER_ID = 677594
SEASON = 2025


def make_identity(**overrides: object) -> PlayerIdentity:
    base = {
        "player_id": PLAYER_ID,
        "full_name": "Julio Rodriguez",
        "primary_position": "CF",
    }
    base.update(overrides)
    return PlayerIdentity(**base)


def make_hitting(**overrides: object) -> PlayerSeasonHitting:
    base = {
        "player_id": PLAYER_ID,
        "season": SEASON,
        "games_played": 150,
        "plate_appearances": 600,
        "at_bats": 500,
        "runs": 80,
        "hits": 150,
        "doubles": 30,
        "triples": 3,
        "home_runs": 20,
        "rbi": 90,
        "base_on_balls": 60,
        "intentional_walks": 5,
        "hit_by_pitch": 5,
        "strikeouts": 100,
        "stolen_bases": 10,
        "caught_stealing": 3,
        "sac_flies": 4,
        "sac_bunts": 2,
    }
    base.update(overrides)
    return PlayerSeasonHitting(**base)


def test_valid_identity_is_accepted() -> None:
    identity = make_identity()
    assert identity.player_id == PLAYER_ID
    assert identity.full_name == "Julio Rodriguez"
    assert identity.primary_position == "CF"


def test_two_way_player_position_abbreviation_is_preserved() -> None:
    """The MLB-reported abbreviation is stored exactly, never normalized."""
    identity = make_identity(primary_position="TWP")
    assert identity.primary_position == "TWP"


@pytest.mark.parametrize("player_id", [0, -1])
def test_nonpositive_player_id_is_rejected(player_id: int) -> None:
    with pytest.raises(ValidationError):
        make_identity(player_id=player_id)


def test_blank_full_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_identity(full_name="")


def test_blank_primary_position_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_identity(primary_position="")


def test_identity_is_frozen() -> None:
    identity = make_identity()
    with pytest.raises(ValidationError):
        identity.full_name = "Someone Else"


def test_valid_season_hitting_is_accepted() -> None:
    hitting = make_hitting()
    assert hitting.hits == 150


@pytest.mark.parametrize(
    "field",
    [
        "games_played",
        "plate_appearances",
        "at_bats",
        "runs",
        "hits",
        "doubles",
        "triples",
        "home_runs",
        "rbi",
        "base_on_balls",
        "intentional_walks",
        "hit_by_pitch",
        "strikeouts",
        "stolen_bases",
        "caught_stealing",
        "sac_flies",
        "sac_bunts",
    ],
)
def test_negative_counting_stats_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        make_hitting(**{field: -1})


def test_at_bats_exceeding_plate_appearances_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_hitting(plate_appearances=400, at_bats=500)


def test_at_bats_equal_to_plate_appearances_is_accepted() -> None:
    hitting = make_hitting(plate_appearances=500, at_bats=500)
    assert hitting.at_bats == hitting.plate_appearances


def test_extra_base_hits_exceeding_hits_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_hitting(hits=10, doubles=5, triples=3, home_runs=5)


def test_intentional_walks_exceeding_base_on_balls_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_hitting(base_on_balls=5, intentional_walks=10)


@pytest.mark.parametrize("season", [0, -1])
def test_nonpositive_season_is_rejected(season: int) -> None:
    with pytest.raises(ValidationError):
        make_hitting(season=season)


def test_season_hitting_is_frozen() -> None:
    hitting = make_hitting()
    with pytest.raises(ValidationError):
        hitting.hits = 200
