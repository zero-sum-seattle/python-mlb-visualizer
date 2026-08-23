"""create_team_game_pitching_lines

Adds the pitching counterpart to ``team_game_batting_lines``.

A new table rather than more columns on the batting line. The two are separate
MLB stat groups fetched in separate requests, and half of each one's columns
would be meaningless on the other row. It also means a season imported before
pitching existed simply has no pitching rows, instead of a batting row full of
nulls — so unlike the batting strikeout and baserunner columns, nothing here
needs a nullable-until-backfilled state.

Because this creates a table rather than altering one, it needs no SQLite batch
rebuild and no hand-copied pre-image of a prior schema. The downgrade is a
straight drop.

The innings column is ``outs``, an integer. MLB returns ``inningsPitched`` as a
string in baseball notation where ``'10.2'`` means ten and two-thirds innings,
so storing it as a number would silently corrupt every derived rate. Only raw
components are stored; ERA, WHIP, K/9, and BB/9 are derived on read.

Revision ID: 27a202039134
Revises: 2efdbec9b07e
Create Date: 2026-08-23 11:13:14.102544

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "27a202039134"
down_revision: str | Sequence[str] | None = "2efdbec9b07e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "team_game_pitching_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_pk", sa.Integer(), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("team_name", sa.String(), nullable=False),
        sa.Column("opponent_id", sa.Integer(), nullable=False),
        sa.Column("opponent_name", sa.String(), nullable=False),
        sa.Column("home_away", sa.String(), nullable=False),
        sa.Column("outs", sa.Integer(), nullable=False),
        sa.Column("hits_allowed", sa.Integer(), nullable=False),
        sa.Column("runs_allowed", sa.Integer(), nullable=False),
        sa.Column("earned_runs", sa.Integer(), nullable=False),
        sa.Column("pitching_base_on_balls", sa.Integer(), nullable=False),
        sa.Column("pitching_strikeouts", sa.Integer(), nullable=False),
        sa.Column("home_runs_allowed", sa.Integer(), nullable=False),
        sa.Column("batters_faced", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("game_number", sa.Integer(), nullable=False),
        sa.Column("doubleheader", sa.Boolean(), nullable=False),
        sa.Column("scheduled_innings", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "home_away IN ('home', 'away')",
            name=op.f("ck_team_game_pitching_lines_pitching_home_away_valid"),
        ),
        sa.CheckConstraint(
            "batters_faced >= 0",
            name=op.f("ck_team_game_pitching_lines_batters_faced_nonnegative"),
        ),
        # The three relational checks below are definitional rather than
        # empirical: an earned run is a run, a home run is a hit, and every out
        # is recorded against a batter faced.
        sa.CheckConstraint(
            "batters_faced >= outs",
            name=op.f("ck_team_game_pitching_lines_batters_faced_covers_outs"),
        ),
        sa.CheckConstraint(
            "earned_runs <= runs_allowed",
            name=op.f("ck_team_game_pitching_lines_earned_runs_within_runs_allowed"),
        ),
        sa.CheckConstraint(
            "home_runs_allowed <= hits_allowed",
            name=op.f("ck_team_game_pitching_lines_home_runs_within_hits_allowed"),
        ),
        sa.CheckConstraint(
            "earned_runs >= 0",
            name=op.f("ck_team_game_pitching_lines_earned_runs_nonnegative"),
        ),
        sa.CheckConstraint(
            "game_number >= 1",
            name=op.f("ck_team_game_pitching_lines_pitching_game_number_min"),
        ),
        sa.CheckConstraint(
            "game_pk > 0",
            name=op.f("ck_team_game_pitching_lines_pitching_game_pk_positive"),
        ),
        sa.CheckConstraint(
            "hits_allowed >= 0",
            name=op.f("ck_team_game_pitching_lines_hits_allowed_nonnegative"),
        ),
        sa.CheckConstraint(
            "home_runs_allowed >= 0",
            name=op.f("ck_team_game_pitching_lines_home_runs_allowed_nonnegative"),
        ),
        sa.CheckConstraint(
            "opponent_id > 0",
            name=op.f("ck_team_game_pitching_lines_pitching_opponent_id_positive"),
        ),
        sa.CheckConstraint(
            "outs >= 0", name=op.f("ck_team_game_pitching_lines_outs_nonnegative")
        ),
        sa.CheckConstraint(
            "pitching_base_on_balls >= 0",
            name=op.f("ck_team_game_pitching_lines_pitching_base_on_balls_nonnegative"),
        ),
        sa.CheckConstraint(
            "pitching_strikeouts >= 0",
            name=op.f("ck_team_game_pitching_lines_pitching_strikeouts_nonnegative"),
        ),
        sa.CheckConstraint(
            "runs_allowed >= 0",
            name=op.f("ck_team_game_pitching_lines_runs_allowed_nonnegative"),
        ),
        sa.CheckConstraint(
            "scheduled_innings >= 1",
            name=op.f("ck_team_game_pitching_lines_pitching_scheduled_innings_min"),
        ),
        sa.CheckConstraint(
            "season > 0",
            name=op.f("ck_team_game_pitching_lines_pitching_season_positive"),
        ),
        sa.CheckConstraint(
            "team_id > 0",
            name=op.f("ck_team_game_pitching_lines_pitching_team_id_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_game_pitching_lines")),
        sa.UniqueConstraint(
            "team_id",
            "game_pk",
            name="uq_team_game_pitching_lines_team_id_game_pk",
        ),
    )
    op.create_index(
        "ix_team_game_pitching_lines_team_season_order",
        "team_game_pitching_lines",
        ["team_id", "season", "game_date", "game_number", "game_pk"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the table outright. Pitching lines are re-fetchable from MLB by
    re-running the import, so nothing unrecoverable is lost — unlike the
    column-adding migrations, this one has no data to preserve on the way down.
    """
    op.drop_index(
        "ix_team_game_pitching_lines_team_season_order",
        table_name="team_game_pitching_lines",
    )
    op.drop_table("team_game_pitching_lines")
