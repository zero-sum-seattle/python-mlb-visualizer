"""Normalized schemas for player identity and player-season hitting stats."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlayerIdentity(BaseModel):
    """A player's persisted identity fields.

    ``primary_position`` stores the MLB-reported position abbreviation exactly
    as returned (for example ``"TWP"`` for a two-way player), never normalized
    into another position.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    player_id: int = Field(gt=0, description="MLB person id.")
    full_name: str = Field(min_length=1, description="Player's full name.")
    primary_position: str = Field(
        min_length=1, description="MLB-reported primary position abbreviation."
    )


class PlayerSeasonHitting(BaseModel):
    """One player's raw hitting counting stats for one MLB season.

    Only raw components are stored. Batting average, OBP, SLG, OPS, and total
    bases are calculated from these fields on demand rather than persisted, so
    a stored rate can never drift from the components it came from.

    Represents the full-season aggregate: a player who played for more than
    one club in a season is stored once, as the combined total, not once per
    team. See ``app.services.players`` for how that aggregate is selected from
    the MLB response.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    player_id: int = Field(gt=0, description="MLB person id.")
    season: int = Field(gt=0, description="Season the stats belong to.")
    games_played: int = Field(ge=0)
    plate_appearances: int = Field(ge=0)
    at_bats: int = Field(ge=0)
    runs: int = Field(ge=0)
    hits: int = Field(ge=0)
    doubles: int = Field(ge=0)
    triples: int = Field(ge=0)
    home_runs: int = Field(ge=0)
    rbi: int = Field(ge=0)
    base_on_balls: int = Field(ge=0)
    intentional_walks: int = Field(ge=0)
    hit_by_pitch: int = Field(ge=0)
    strikeouts: int = Field(ge=0)
    stolen_bases: int = Field(ge=0)
    caught_stealing: int = Field(ge=0)
    sac_flies: int = Field(ge=0)
    sac_bunts: int = Field(ge=0)

    @model_validator(mode="after")
    def _counting_stats_are_internally_consistent(self) -> PlayerSeasonHitting:
        """Reject a season whose components contradict each other.

        These are definitional relationships, not empirical ones: an at-bat is
        a plate appearance, an extra-base hit is a hit, and an intentional walk
        is a walk. Spot-checked across real single-team, two-way, and
        traded-player seasons with no violations before being encoded here.
        """
        if self.at_bats > self.plate_appearances:
            raise ValueError(
                f"at_bats ({self.at_bats}) cannot exceed plate_appearances "
                f"({self.plate_appearances})"
            )
        extra_base_hits = self.doubles + self.triples + self.home_runs
        if extra_base_hits > self.hits:
            raise ValueError(
                f"doubles + triples + home_runs ({extra_base_hits}) cannot "
                f"exceed hits ({self.hits})"
            )
        if self.intentional_walks > self.base_on_balls:
            raise ValueError(
                f"intentional_walks ({self.intentional_walks}) cannot exceed "
                f"base_on_balls ({self.base_on_balls})"
            )
        return self
