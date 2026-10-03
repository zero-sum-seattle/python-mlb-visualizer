"""Normalized schemas for player identity and player-season hitting stats."""

from __future__ import annotations

import unicodedata

from pydantic import BaseModel, ConfigDict, Field, model_validator


def normalize_player_name(name: str) -> str:
    """Fold case and accents consistently for catalog ordering and search."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


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
        return (normalize_player_name(self.full_name), self.full_name, self.player_id)


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
        a plate appearance, a hit and a batting strikeout each end an at-bat
        (a strikeout is charged as an at-bat even when the batter reaches on a
        dropped third strike), a walk ends a plate appearance, an extra-base
        hit is a hit, and an intentional walk is a walk. The at-bat,
        extra-base-hit, and intentional-walk rules were spot-checked across
        real single-team, two-way, and traded-player seasons before being
        encoded; the hits, strikeout, and walk rules (issue #64) follow from
        the official scoring definitions alone.

        The set is deliberately minimal. Bounds implied by these -- strikeouts
        and home runs within plate appearances, home runs within at-bats -- are
        not checked separately. Together the rules bound every numerator of the
        rates in ``app.analytics.player_hitting`` by its denominator, so a line
        that passes here can always be summarized.

        ``player_season_hitting`` mirrors only the at-bat, extra-base-hit, and
        intentional-walk rules as CHECK constraints; the hits, strikeout, and
        walk rules are enforced here only. See "Hitting line integrity" in
        ``docs/ui-information-architecture.md`` for why.
        """
        for count, count_name, total, total_name in (
            (self.at_bats, "at_bats", self.plate_appearances, "plate_appearances"),
            (self.hits, "hits", self.at_bats, "at_bats"),
            (self.strikeouts, "strikeouts", self.at_bats, "at_bats"),
            (
                self.base_on_balls,
                "base_on_balls",
                self.plate_appearances,
                "plate_appearances",
            ),
            (
                self.doubles + self.triples + self.home_runs,
                "doubles + triples + home_runs",
                self.hits,
                "hits",
            ),
            (
                self.intentional_walks,
                "intentional_walks",
                self.base_on_balls,
                "base_on_balls",
            ),
        ):
            if count > total:
                raise ValueError(
                    f"{count_name} ({count}) cannot exceed {total_name} ({total})"
                )
        return self
