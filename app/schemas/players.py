"""Normalized schemas for player identity and player-season hitting stats."""

from __future__ import annotations

import unicodedata

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

    def name_sort_key(self) -> tuple[str, str, int]:
        """Return this player's position in alphabetical-by-name display order.

        MLB rosters contain many accented names, and both of the orderings
        available by default place them after every unaccented name: SQLite's
        default collation compares raw bytes, and Python's ``casefold`` lowers
        case without folding accents. Neither puts ``Ángel Martínez`` under A.

        Ordering therefore lives here rather than in an ``ORDER BY`` clause, so
        that MLB discovery and the persisted catalog return the same players in
        the same order. ``full_name`` breaks ties between names that fold
        together (``Zoë`` and ``zoë``) and ``player_id`` makes the result total.
        """
        decomposed = unicodedata.normalize("NFKD", self.full_name)
        folded = "".join(
            character
            for character in decomposed
            if not unicodedata.combining(character)
        ).casefold()
        return (folded, self.full_name, self.player_id)


class PlayerSeasonCatalogEntry(PlayerIdentity):
    """One MLB player identity associated with one season's player directory.

    The season scopes membership, not the identity fields. MLB's historical
    ``get_people`` responses return current biographical details for a person,
    so ``full_name`` and ``primary_position`` continue to live on the global
    player identity while this model adds the season in which MLB listed that
    person as a Major League player.
    """

    season: int = Field(gt=0, description="MLB season directory membership.")

    def to_identity(self) -> PlayerIdentity:
        """Return the global identity portion used by ``players`` persistence."""
        return PlayerIdentity(
            player_id=self.player_id,
            full_name=self.full_name,
            primary_position=self.primary_position,
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
