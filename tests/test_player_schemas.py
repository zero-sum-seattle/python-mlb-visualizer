"""Tests for player identity and player-season hitting domain schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.players import (
    PlayerIdentity,
    PlayerSeasonCatalogEntry,
    PlayerSeasonHitting,
)

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


def test_catalog_entry_preserves_identity_and_adds_season() -> None:
    entry = PlayerSeasonCatalogEntry(
        player_id=677594,
        full_name="Julio Rodríguez",
        primary_position="CF",
        season=2025,
    )
    assert entry.to_identity() == make_identity(full_name="Julio Rodríguez")


def test_name_sort_key_folds_case_and_accents() -> None:
    folded, original, player_id = make_identity(
        full_name="Ángel Martínez"
    ).name_sort_key()
    assert folded == "angel martinez"
    assert original == "Ángel Martínez"
    assert player_id == PLAYER_ID


def test_name_sort_key_breaks_folded_ties_by_name_then_id() -> None:
    names = [
        make_identity(player_id=2, full_name="zoë"),
        make_identity(player_id=1, full_name="zoë"),
        make_identity(player_id=9, full_name="Zoë"),
    ]
    ordered = sorted(names, key=PlayerIdentity.name_sort_key)
    assert [(one.full_name, one.player_id) for one in ordered] == [
        ("Zoë", 9),
        ("zoë", 1),
        ("zoë", 2),
    ]


def test_catalog_entry_requires_positive_season() -> None:
    with pytest.raises(ValidationError):
        PlayerSeasonCatalogEntry(
            player_id=677594,
            full_name="Julio Rodríguez",
            primary_position="CF",
            season=0,
        )


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


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"at_bats": 100, "hits": 101}, r"hits \(101\) cannot exceed at_bats \(100\)"),
        (
            {"at_bats": 100, "hits": 60, "strikeouts": 101},
            r"strikeouts \(101\) cannot exceed at_bats \(100\)",
        ),
        (
            # Within at-bats would already fail; keep at-bats out of the way so
            # only the walk rule is exercised.
            {
                "plate_appearances": 600,
                "at_bats": 0,
                "hits": 0,
                "doubles": 0,
                "triples": 0,
                "home_runs": 0,
                "strikeouts": 0,
                "base_on_balls": 601,
            },
            r"base_on_balls \(601\) cannot exceed plate_appearances \(600\)",
        ),
    ],
    ids=["hits-over-ab", "so-over-ab", "bb-over-pa"],
)
def test_counts_exceeding_the_totals_that_contain_them_are_rejected(
    overrides: dict[str, int], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        make_hitting(**overrides)


def test_strikeouts_over_plate_appearances_is_rejected_by_the_at_bat_rule() -> None:
    """SO <= PA is implied by SO <= AB <= PA and is not a separate rule."""
    with pytest.raises(ValidationError, match="strikeouts"):
        make_hitting(plate_appearances=600, at_bats=500, strikeouts=601)


def test_home_runs_over_at_bats_is_rejected_through_hits() -> None:
    """HR <= H <= AB <= PA, so no separate home-run bound exists."""
    with pytest.raises(ValidationError, match="hits"):
        make_hitting(at_bats=10, hits=12, doubles=0, triples=0, home_runs=12)


@pytest.mark.parametrize(
    "overrides",
    [
        {"at_bats": 150, "hits": 150, "strikeouts": 100},
        {"at_bats": 100, "hits": 60, "strikeouts": 100},
        {
            "plate_appearances": 60,
            "at_bats": 0,
            "hits": 0,
            "doubles": 0,
            "triples": 0,
            "home_runs": 0,
            "strikeouts": 0,
            "base_on_balls": 60,
        },
        {
            "plate_appearances": 500,
            "at_bats": 500,
            "base_on_balls": 0,
            "intentional_walks": 0,
        },
        {"hits": 53, "doubles": 30, "triples": 3, "home_runs": 20},
        {"base_on_balls": 5, "intentional_walks": 5},
    ],
    ids=[
        "hits-equal-ab",
        "so-equal-ab",
        "bb-equal-pa",
        "ab-equal-pa",
        "xbh-equal-hits",
        "ibb-equal-bb",
    ],
)
def test_counts_equal_to_their_totals_are_accepted(
    overrides: dict[str, int],
) -> None:
    make_hitting(**overrides)


def test_zero_plate_appearances_is_accepted() -> None:
    """A pitcher listed in the hitting group without batting is a real line."""
    zeros = dict.fromkeys(
        (
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
        ),
        0,
    )
    hitting = make_hitting(games_played=3, **zeros)
    assert hitting.plate_appearances == 0


def test_zero_at_bats_with_plate_appearances_is_accepted() -> None:
    hitting = make_hitting(
        plate_appearances=3,
        at_bats=0,
        hits=0,
        doubles=0,
        triples=0,
        home_runs=0,
        strikeouts=0,
        base_on_balls=2,
        intentional_walks=1,
        hit_by_pitch=0,
        sac_flies=1,
        sac_bunts=0,
    )
    assert hitting.at_bats == 0


@pytest.mark.parametrize(
    "overrides",
    [
        # Representative single-team regular.
        {},
        # Traded-player full-season aggregate, as in test_players_service.
        {"hits": 127, "at_bats": 524, "plate_appearances": 664},
        # Two-way player: heavy walk and strikeout totals, many intentional
        # walks. The hitting line has the same shape as anyone else's.
        {
            "games_played": 135,
            "plate_appearances": 599,
            "at_bats": 497,
            "hits": 151,
            "doubles": 26,
            "triples": 8,
            "home_runs": 44,
            "base_on_balls": 91,
            "intentional_walks": 21,
            "hit_by_pitch": 3,
            "strikeouts": 143,
            "sac_flies": 8,
            "sac_bunts": 0,
        },
    ],
    ids=["single-team", "traded-aggregate", "two-way"],
)
def test_realistic_season_lines_remain_valid(overrides: dict[str, int]) -> None:
    make_hitting(**overrides)


@pytest.mark.parametrize("season", [0, -1])
def test_nonpositive_season_is_rejected(season: int) -> None:
    with pytest.raises(ValidationError):
        make_hitting(season=season)


def test_season_hitting_is_frozen() -> None:
    hitting = make_hitting()
    with pytest.raises(ValidationError):
        hitting.hits = 200
