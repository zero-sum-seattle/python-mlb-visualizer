"""add_baserunner_components_to_team_game_batting_lines

Adds the two raw components a "baserunners" page needs that are not already
stored: the team's walks (``base_on_balls``) and hit-by-pitch total per game.
Baserunners itself (hits + walks + hit-by-pitch) is calculated in the
analytics layer, not stored as its own column, matching how every other
derived number in this application works.

Both columns are nullable and have no default. Rows written before this
revision were ingested without them, so their real totals are unknown;
recording them as 0 would fabricate games in which nobody walked or was hit by
a pitch. They stay NULL until the team-season is re-imported from MLB, which
backfills the real values.

SQLite cannot attach a CHECK constraint with ``ALTER TABLE``, so the table is
rebuilt in batch mode, following the same approach revision 94dec6973c80 used
to add ``strikeouts``. ``copy_from`` describes the pre-revision table in full,
including the constraints SQLite reflection does not return, so the rebuild
preserves them instead of quietly dropping them.

Revision ID: 2efdbec9b07e
Revises: 7f2c4b8e91d3
Create Date: 2026-08-22 11:58:54.224629

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2efdbec9b07e"
down_revision: str | Sequence[str] | None = "7f2c4b8e91d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "team_game_batting_lines"
INDEX_NAME = "ix_team_game_batting_lines_team_season_order"
STRIKEOUTS_CONSTRAINT = "strikeouts_nonnegative_or_unknown"
BASE_ON_BALLS_CONSTRAINT = "base_on_balls_nonnegative_or_unknown"
HIT_BY_PITCH_CONSTRAINT = "hit_by_pitch_nonnegative_or_unknown"

# Repeated here rather than imported from ``app.database.base`` so this
# revision keeps describing the schema it was written against even if the
# application's convention later changes.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _table_before_this_revision() -> sa.Table:
    """Describe the table as revision 94dec6973c80 left it.

    Written out rather than reflected because the SQLite dialect does not
    reflect CHECK constraints; a batch rebuild driven by reflection alone would
    silently drop the hits, runs, home/away, and strikeouts integrity rules.
    """
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    table = sa.Table(
        TABLE_NAME,
        metadata,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_pk", sa.Integer(), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("team_name", sa.String(), nullable=False),
        sa.Column("opponent_id", sa.Integer(), nullable=False),
        sa.Column("opponent_name", sa.String(), nullable=False),
        sa.Column("home_away", sa.String(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False),
        sa.Column("strikeouts", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("game_number", sa.Integer(), nullable=False),
        sa.Column("doubleheader", sa.Boolean(), nullable=False),
        sa.Column("scheduled_innings", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "home_away IN ('home', 'away')",
            name="home_away_valid",
        ),
        sa.CheckConstraint(
            "game_number >= 1",
            name="game_number_min",
        ),
        sa.CheckConstraint(
            "game_pk > 0",
            name="game_pk_positive",
        ),
        sa.CheckConstraint(
            "hits >= 0",
            name="hits_nonnegative",
        ),
        sa.CheckConstraint(
            "opponent_id > 0",
            name="opponent_id_positive",
        ),
        sa.CheckConstraint(
            "runs >= 0",
            name="runs_nonnegative",
        ),
        sa.CheckConstraint(
            "strikeouts IS NULL OR strikeouts >= 0",
            name=STRIKEOUTS_CONSTRAINT,
        ),
        sa.CheckConstraint(
            "scheduled_innings >= 1",
            name="scheduled_innings_min",
        ),
        sa.CheckConstraint(
            "season > 0",
            name="season_positive",
        ),
        sa.CheckConstraint(
            "team_id > 0",
            name="team_id_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "team_id",
            "game_pk",
            name="uq_team_game_batting_lines_team_id_game_pk",
        ),
    )
    sa.Index(
        INDEX_NAME,
        table.c.team_id,
        table.c.season,
        table.c.game_date,
        table.c.game_number,
        table.c.game_pk,
    )
    return table


def upgrade() -> None:
    """Add the nullable baserunner-component columns and their constraints."""
    with op.batch_alter_table(
        TABLE_NAME,
        copy_from=_table_before_this_revision(),
        recreate="always",
    ) as batch_op:
        batch_op.add_column(sa.Column("base_on_balls", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("hit_by_pitch", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            BASE_ON_BALLS_CONSTRAINT,
            "base_on_balls IS NULL OR base_on_balls >= 0",
        )
        batch_op.create_check_constraint(
            HIT_BY_PITCH_CONSTRAINT,
            "hit_by_pitch IS NULL OR hit_by_pitch >= 0",
        )


def downgrade() -> None:
    """Drop the two columns, returning to the pre-baserunners schema.

    Recorded walk and hit-by-pitch values are lost, which is the honest
    outcome: the previous schema has nowhere to keep them. Every other column
    is copied across unchanged.
    """
    with op.batch_alter_table(
        TABLE_NAME,
        copy_from=_table_after_this_revision(),
        recreate="always",
    ) as batch_op:
        batch_op.drop_constraint(HIT_BY_PITCH_CONSTRAINT, type_="check")
        batch_op.drop_constraint(BASE_ON_BALLS_CONSTRAINT, type_="check")
        batch_op.drop_column("hit_by_pitch")
        batch_op.drop_column("base_on_balls")


def _table_after_this_revision() -> sa.Table:
    """Describe the table as this revision leaves it."""
    table = _table_before_this_revision()
    table.append_column(sa.Column("base_on_balls", sa.Integer(), nullable=True))
    table.append_column(sa.Column("hit_by_pitch", sa.Integer(), nullable=True))
    table.append_constraint(
        sa.CheckConstraint(
            "base_on_balls IS NULL OR base_on_balls >= 0",
            name=BASE_ON_BALLS_CONSTRAINT,
        )
    )
    table.append_constraint(
        sa.CheckConstraint(
            "hit_by_pitch IS NULL OR hit_by_pitch >= 0",
            name=HIT_BY_PITCH_CONSTRAINT,
        )
    )
    return table
