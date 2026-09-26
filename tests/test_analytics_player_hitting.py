"""Tests for season-level Player hitting rates.

Expected values are worked by hand from the stored components so each formula
is checked independently of the implementation. The zero-denominator cases
hold the rule this module exists for: an undefined rate is None, never 0.0.
"""

import pytest
from pydantic import ValidationError

from app.analytics.player_hitting import (
    PlayerHittingAnalysisError,
    build_player_hitting_overview,
)
from app.schemas.analytics import PlayerHittingOverview
from tests.test_repositories_players import make_hitting

NO_PLATE_APPEARANCES = {
    "games_played": 1,
    "plate_appearances": 0,
    "at_bats": 0,
    "runs": 0,
    "hits": 0,
    "doubles": 0,
    "triples": 0,
    "home_runs": 0,
    "rbi": 0,
    "base_on_balls": 0,
    "intentional_walks": 0,
    "hit_by_pitch": 0,
    "strikeouts": 0,
    "stolen_bases": 0,
    "caught_stealing": 0,
    "sac_flies": 0,
    "sac_bunts": 0,
}


class TestFormulas:
    """make_hitting: 600 PA, 500 AB, 150 H (30 2B, 3 3B, 20 HR), 60 BB,
    5 HBP, 4 SF, 2 SH, 100 SO."""

    def test_total_bases_counts_every_base(self) -> None:
        # 97 singles + 2 * 30 + 3 * 3 + 4 * 20 = 97 + 60 + 9 + 80 = 246.
        overview = build_player_hitting_overview(make_hitting())
        assert overview.total_bases == 246

    def test_batting_average_is_hits_per_at_bat(self) -> None:
        overview = build_player_hitting_overview(make_hitting())
        assert overview.batting_average == pytest.approx(150 / 500)

    def test_on_base_percentage_uses_the_standard_denominator(self) -> None:
        # (150 + 60 + 5) / (500 + 60 + 5 + 4) = 215 / 569.
        overview = build_player_hitting_overview(make_hitting())
        assert overview.on_base_percentage == pytest.approx(215 / 569)

    def test_sacrifice_bunts_are_not_in_the_obp_denominator(self) -> None:
        few = build_player_hitting_overview(make_hitting(sac_bunts=0))
        many = build_player_hitting_overview(make_hitting(sac_bunts=20))
        assert few.on_base_percentage == many.on_base_percentage

    def test_slugging_is_total_bases_per_at_bat(self) -> None:
        overview = build_player_hitting_overview(make_hitting())
        assert overview.slugging_percentage == pytest.approx(246 / 500)

    def test_ops_is_obp_plus_slg(self) -> None:
        overview = build_player_hitting_overview(make_hitting())
        assert overview.on_base_plus_slugging == pytest.approx(215 / 569 + 246 / 500)

    def test_rates_keep_full_precision(self) -> None:
        overview = build_player_hitting_overview(make_hitting())
        assert overview.on_base_percentage == 215 / 569
        assert round(overview.on_base_percentage, 3) != overview.on_base_percentage

    def test_plate_appearance_rates_share_one_denominator(self) -> None:
        rates = build_player_hitting_overview(make_hitting()).plate_appearance_rates
        assert rates is not None
        assert rates.plate_appearances == 600
        assert rates.strikeout_rate == pytest.approx(100 / 600)
        assert rates.walk_rate == pytest.approx(60 / 600)
        assert rates.home_run_rate == pytest.approx(20 / 600)

    def test_stored_line_is_carried_unchanged(self) -> None:
        hitting = make_hitting()
        assert build_player_hitting_overview(hitting).hitting == hitting


class TestUndefinedRates:
    def test_no_plate_appearances_leaves_every_rate_undefined(self) -> None:
        overview = build_player_hitting_overview(make_hitting(**NO_PLATE_APPEARANCES))
        assert overview.total_bases == 0
        assert overview.batting_average is None
        assert overview.on_base_percentage is None
        assert overview.slugging_percentage is None
        assert overview.on_base_plus_slugging is None
        assert overview.plate_appearance_rates is None

    def test_walks_without_at_bats_define_obp_but_not_avg_slg_or_ops(self) -> None:
        overview = build_player_hitting_overview(
            make_hitting(
                **{
                    **NO_PLATE_APPEARANCES,
                    "plate_appearances": 3,
                    "base_on_balls": 2,
                    "hit_by_pitch": 1,
                }
            )
        )
        assert overview.batting_average is None
        assert overview.slugging_percentage is None
        assert overview.on_base_percentage == pytest.approx(1.0)
        assert overview.on_base_plus_slugging is None
        assert overview.plate_appearance_rates is not None
        assert overview.plate_appearance_rates.walk_rate == pytest.approx(2 / 3)

    def test_a_lone_sacrifice_fly_is_a_real_zero_obp(self) -> None:
        overview = build_player_hitting_overview(
            make_hitting(
                **{**NO_PLATE_APPEARANCES, "plate_appearances": 1, "sac_flies": 1}
            )
        )
        assert overview.on_base_percentage == 0.0
        assert overview.on_base_percentage is not None
        assert overview.batting_average is None

    def test_hitless_at_bats_are_a_real_zero_not_undefined(self) -> None:
        overview = build_player_hitting_overview(
            make_hitting(
                **{
                    **NO_PLATE_APPEARANCES,
                    "plate_appearances": 4,
                    "at_bats": 4,
                    "strikeouts": 4,
                }
            )
        )
        assert overview.batting_average == 0.0
        assert overview.slugging_percentage == 0.0
        assert overview.on_base_percentage == 0.0
        assert overview.on_base_plus_slugging == 0.0
        assert overview.plate_appearance_rates is not None
        assert overview.plate_appearance_rates.strikeout_rate == 1.0
        assert overview.plate_appearance_rates.home_run_rate == 0.0


def test_more_hits_than_at_bats_is_a_data_integrity_error() -> None:
    hitting = make_hitting(
        **{**NO_PLATE_APPEARANCES, "plate_appearances": 10, "at_bats": 3, "hits": 5}
    )
    with pytest.raises(PlayerHittingAnalysisError, match="5 hits in 3 at-bats"):
        build_player_hitting_overview(hitting)


class TestOverviewSchema:
    def _dump(self, **overrides: object) -> dict[str, object]:
        dumped = build_player_hitting_overview(make_hitting()).model_dump()
        dumped.update(overrides)
        return dumped

    def test_rejects_a_drifted_rate(self) -> None:
        with pytest.raises(ValidationError, match="batting_average"):
            PlayerHittingOverview.model_validate(self._dump(batting_average=0.301))

    def test_rejects_wrong_total_bases(self) -> None:
        with pytest.raises(ValidationError, match="total_bases"):
            PlayerHittingOverview.model_validate(self._dump(total_bases=245))

    def test_rejects_zero_in_place_of_an_undefined_rate(self) -> None:
        dumped = build_player_hitting_overview(
            make_hitting(**NO_PLATE_APPEARANCES)
        ).model_dump()
        dumped["batting_average"] = 0.0
        with pytest.raises(ValidationError, match="must be None"):
            PlayerHittingOverview.model_validate(dumped)

    def test_rejects_ops_without_both_components(self) -> None:
        dumped = build_player_hitting_overview(
            make_hitting(
                **{**NO_PLATE_APPEARANCES, "plate_appearances": 1, "base_on_balls": 1}
            )
        ).model_dump()
        dumped["on_base_plus_slugging"] = 1.0
        with pytest.raises(ValidationError, match="on_base_plus_slugging"):
            PlayerHittingOverview.model_validate(dumped)

    def test_rejects_rates_detached_from_the_line(self) -> None:
        dumped = self._dump()
        dumped["plate_appearance_rates"] = None
        with pytest.raises(ValidationError, match="plate_appearance_rates"):
            PlayerHittingOverview.model_validate(dumped)
