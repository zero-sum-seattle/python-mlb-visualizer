"""create_players_and_player_season_hitting

Revision ID: 73d9fae8fafb
Revises: 27a202039134
Create Date: 2026-08-28 23:53:40.528933

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "73d9fae8fafb"
down_revision: str | Sequence[str] | None = "27a202039134"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("primary_position", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("player_id > 0", name=op.f("ck_players_player_id_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_players")),
        sa.UniqueConstraint("player_id", name="uq_players_player_id"),
    )
    op.create_table(
        "player_season_hitting",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("games_played", sa.Integer(), nullable=False),
        sa.Column("plate_appearances", sa.Integer(), nullable=False),
        sa.Column("at_bats", sa.Integer(), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("doubles", sa.Integer(), nullable=False),
        sa.Column("triples", sa.Integer(), nullable=False),
        sa.Column("home_runs", sa.Integer(), nullable=False),
        sa.Column("rbi", sa.Integer(), nullable=False),
        sa.Column("base_on_balls", sa.Integer(), nullable=False),
        sa.Column("intentional_walks", sa.Integer(), nullable=False),
        sa.Column("hit_by_pitch", sa.Integer(), nullable=False),
        sa.Column("strikeouts", sa.Integer(), nullable=False),
        sa.Column("stolen_bases", sa.Integer(), nullable=False),
        sa.Column("caught_stealing", sa.Integer(), nullable=False),
        sa.Column("sac_flies", sa.Integer(), nullable=False),
        sa.Column("sac_bunts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "player_id > 0", name=op.f("ck_player_season_hitting_player_id_positive")
        ),
        sa.CheckConstraint(
            "season > 0", name=op.f("ck_player_season_hitting_season_positive")
        ),
        sa.CheckConstraint(
            "games_played >= 0",
            name=op.f("ck_player_season_hitting_games_played_nonnegative"),
        ),
        sa.CheckConstraint(
            "plate_appearances >= 0",
            name=op.f("ck_player_season_hitting_plate_appearances_nonnegative"),
        ),
        sa.CheckConstraint(
            "at_bats >= 0", name=op.f("ck_player_season_hitting_at_bats_nonnegative")
        ),
        sa.CheckConstraint(
            "runs >= 0", name=op.f("ck_player_season_hitting_runs_nonnegative")
        ),
        sa.CheckConstraint(
            "hits >= 0", name=op.f("ck_player_season_hitting_hits_nonnegative")
        ),
        sa.CheckConstraint(
            "doubles >= 0", name=op.f("ck_player_season_hitting_doubles_nonnegative")
        ),
        sa.CheckConstraint(
            "triples >= 0", name=op.f("ck_player_season_hitting_triples_nonnegative")
        ),
        sa.CheckConstraint(
            "home_runs >= 0",
            name=op.f("ck_player_season_hitting_home_runs_nonnegative"),
        ),
        sa.CheckConstraint(
            "rbi >= 0", name=op.f("ck_player_season_hitting_rbi_nonnegative")
        ),
        sa.CheckConstraint(
            "base_on_balls >= 0",
            name=op.f("ck_player_season_hitting_base_on_balls_nonnegative"),
        ),
        sa.CheckConstraint(
            "intentional_walks >= 0",
            name=op.f("ck_player_season_hitting_intentional_walks_nonnegative"),
        ),
        sa.CheckConstraint(
            "hit_by_pitch >= 0",
            name=op.f("ck_player_season_hitting_hit_by_pitch_nonnegative"),
        ),
        sa.CheckConstraint(
            "strikeouts >= 0",
            name=op.f("ck_player_season_hitting_strikeouts_nonnegative"),
        ),
        sa.CheckConstraint(
            "stolen_bases >= 0",
            name=op.f("ck_player_season_hitting_stolen_bases_nonnegative"),
        ),
        sa.CheckConstraint(
            "caught_stealing >= 0",
            name=op.f("ck_player_season_hitting_caught_stealing_nonnegative"),
        ),
        sa.CheckConstraint(
            "sac_flies >= 0",
            name=op.f("ck_player_season_hitting_sac_flies_nonnegative"),
        ),
        sa.CheckConstraint(
            "sac_bunts >= 0",
            name=op.f("ck_player_season_hitting_sac_bunts_nonnegative"),
        ),
        # Definitional, not empirical: an at-bat is a plate appearance, an
        # extra-base hit is a hit, and an intentional walk is a walk.
        # Spot-checked against real single-team, two-way, and traded-player
        # seasons before being encoded here.
        sa.CheckConstraint(
            "at_bats <= plate_appearances",
            name=op.f("ck_player_season_hitting_at_bats_within_plate_appearances"),
        ),
        sa.CheckConstraint(
            "doubles + triples + home_runs <= hits",
            name=op.f("ck_player_season_hitting_extra_base_hits_within_hits"),
        ),
        sa.CheckConstraint(
            "intentional_walks <= base_on_balls",
            name=op.f(
                "ck_player_season_hitting_intentional_walks_within_base_on_balls"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.player_id"],
            name=op.f("fk_player_season_hitting_player_id_players"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_season_hitting")),
        sa.UniqueConstraint(
            "player_id",
            "season",
            name="uq_player_season_hitting_player_id_season",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("player_season_hitting")
    op.drop_table("players")
