"""Season-level hitting rates for one player.

Answers one question:

    What did this player's overall offensive season look like?

The input is one stored full-season aggregate, not a list of games, so this is
a single calculation rather than a trend: no rolling windows, no chart points,
and no comparison with MLB. A player who played for more than one club is
described by the combined season line; which clubs those were is not modelled.

Every rate is a ratio of season totals. A rate whose denominator is zero is
undefined and returned as ``None``, never ``0.0``: a player with no at-bats did
not bat ``.000``, and presenting it that way would fabricate a statistic.

Like the rest of the package this layer is free of FastAPI, Jinja, SQLAlchemy,
Plotly, and the MLB API. Values keep full precision; rounding is presentation.
"""

from app.schemas.analytics import PlayerHittingOverview, PlayerPlateAppearanceRates
from app.schemas.players import PlayerSeasonHitting


class PlayerHittingAnalysisError(ValueError):
    """A stored hitting line contradicts itself and cannot be described."""


def build_player_hitting_overview(
    hitting: PlayerSeasonHitting,
) -> PlayerHittingOverview:
    """Derive AVG, OBP, SLG, OPS, and the plate-appearance rate profile.

    Formulas, all over season totals:

    - ``TB  = H + 2B + 2 * 3B + 3 * HR``. ``H`` already counts the first base
      of every hit, so each extra-base hit adds only its extra bases.
    - ``AVG = H / AB``
    - ``OBP = (H + BB + HBP) / (AB + BB + HBP + SF)``. Sacrifice bunts are not
      in the denominator.
    - ``SLG = TB / AB``
    - ``OPS = OBP + SLG``, undefined when either component is.
    - ``K% = SO / PA``, ``BB% = BB / PA``, ``HR% = HR / PA``.

    Raises
    ------
    PlayerHittingAnalysisError
        The line records more hits than at-bats, which no real season can.
    """
    if hitting.hits > hitting.at_bats:
        raise PlayerHittingAnalysisError(
            f"Player {hitting.player_id}'s stored {hitting.season} hitting line "
            f"records {hitting.hits} hits in {hitting.at_bats} at-bats; hits "
            "cannot exceed at-bats"
        )

    total_bases = (
        hitting.hits + hitting.doubles + 2 * hitting.triples + 3 * hitting.home_runs
    )
    batting_average = _ratio(hitting.hits, hitting.at_bats)
    on_base_percentage = _ratio(
        hitting.hits + hitting.base_on_balls + hitting.hit_by_pitch,
        hitting.at_bats
        + hitting.base_on_balls
        + hitting.hit_by_pitch
        + hitting.sac_flies,
    )
    slugging_percentage = _ratio(total_bases, hitting.at_bats)
    on_base_plus_slugging = (
        None
        if on_base_percentage is None or slugging_percentage is None
        else on_base_percentage + slugging_percentage
    )

    return PlayerHittingOverview(
        hitting=hitting,
        total_bases=total_bases,
        batting_average=batting_average,
        on_base_percentage=on_base_percentage,
        slugging_percentage=slugging_percentage,
        on_base_plus_slugging=on_base_plus_slugging,
        plate_appearance_rates=_plate_appearance_rates(hitting),
    )


def _plate_appearance_rates(
    hitting: PlayerSeasonHitting,
) -> PlayerPlateAppearanceRates | None:
    """Return K%, BB%, and HR% together, or None with no plate appearances."""
    plate_appearances = hitting.plate_appearances
    if plate_appearances == 0:
        return None
    return PlayerPlateAppearanceRates(
        plate_appearances=plate_appearances,
        strikeouts=hitting.strikeouts,
        base_on_balls=hitting.base_on_balls,
        home_runs=hitting.home_runs,
        strikeout_rate=hitting.strikeouts / plate_appearances,
        walk_rate=hitting.base_on_balls / plate_appearances,
        home_run_rate=hitting.home_runs / plate_appearances,
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    """Divide, or return None when the rate is undefined."""
    if denominator == 0:
        return None
    return numerator / denominator
