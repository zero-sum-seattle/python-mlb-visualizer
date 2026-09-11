"""create_player_season_catalog

Add the season-membership table used by Player discovery. Existing
``player_season_hitting`` rows are authoritative evidence that their players
belong to those seasons, so those memberships are backfilled without
fabricating any identity or statistic.

Revision ID: 8b3f31d9a5c2
Revises: 73d9fae8fafb
Create Date: 2026-09-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b3f31d9a5c2"
down_revision: str | Sequence[str] | None = "73d9fae8fafb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create player-season membership and backfill known hitting seasons."""
    op.create_table(
        "player_seasons",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "player_id > 0", name=op.f("ck_player_seasons_player_id_positive")
        ),
        sa.CheckConstraint(
            "season > 0", name=op.f("ck_player_seasons_season_positive")
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.player_id"],
            name=op.f("fk_player_seasons_player_id_players"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_seasons")),
        sa.UniqueConstraint(
            "player_id", "season", name="uq_player_seasons_player_id_season"
        ),
    )
    op.create_index(
        "ix_player_seasons_season_player_id",
        "player_seasons",
        ["season", "player_id"],
        unique=False,
    )

    player_seasons = sa.table(
        "player_seasons",
        sa.column("player_id", sa.Integer()),
        sa.column("season", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
    )
    hitting = sa.table(
        "player_season_hitting",
        sa.column("player_id", sa.Integer()),
        sa.column("season", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
    )
    op.execute(
        player_seasons.insert().from_select(
            ["player_id", "season", "created_at"],
            sa.select(hitting.c.player_id, hitting.c.season, hitting.c.created_at),
        )
    )


def downgrade() -> None:
    """Remove Player directory membership while preserving identities and stats."""
    op.drop_index("ix_player_seasons_season_player_id", table_name="player_seasons")
    op.drop_table("player_seasons")
