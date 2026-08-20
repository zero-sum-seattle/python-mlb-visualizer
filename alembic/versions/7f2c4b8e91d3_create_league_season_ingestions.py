"""create_league_season_ingestions

Adds the table that records whether a league-wide ingestion of a season covered
every MLB team discovered for it.

The table is new and separate on purpose. League-import bookkeeping is
operational metadata about a run, not a property of any single team's batting
line, so putting it on ``team_game_batting_lines`` would repeat one run's status
across thousands of game rows and leave no place to record a team that failed
before writing any rows at all. Nothing in ``team_game_batting_lines`` is read,
written, or rebuilt by this revision, so stored game data and the Milestone 3.5
strikeouts column are untouched.

One row per season holds current state rather than an attempt log. The CHECK
constraints keep the coverage claim honest at the database level: a row can only
say COMPLETE when at least one team was expected and none failed, and
``completed_at`` is set if and only if the run is no longer RUNNING.

Revision ID: 7f2c4b8e91d3
Revises: 94dec6973c80
Create Date: 2026-08-20 15:40:11.208414

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f2c4b8e91d3"
down_revision: str | Sequence[str] | None = "94dec6973c80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "league_season_ingestions"


def upgrade() -> None:
    """Create the league-season ingestion coverage table."""
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("expected_team_count", sa.Integer(), nullable=False),
        sa.Column("successful_team_count", sa.Integer(), nullable=False),
        sa.Column("failed_team_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("season > 0", name="season_positive"),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'INCOMPLETE')",
            name="status_valid",
        ),
        sa.CheckConstraint("expected_team_count >= 0", name="expected_team_count_min"),
        sa.CheckConstraint(
            "successful_team_count >= 0", name="successful_team_count_min"
        ),
        sa.CheckConstraint("failed_team_count >= 0", name="failed_team_count_min"),
        sa.CheckConstraint(
            "(status = 'RUNNING') = (completed_at IS NULL)",
            name="completed_at_matches_status",
        ),
        sa.CheckConstraint(
            "status = 'RUNNING' OR "
            "successful_team_count + failed_team_count = expected_team_count",
            name="finished_counts_add_up",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETE' OR "
            "(failed_team_count = 0 AND expected_team_count > 0)",
            name="complete_requires_full_coverage",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season", name="uq_league_season_ingestions_season"),
    )


def downgrade() -> None:
    """Drop the coverage table, returning to the pre-Milestone 4 schema.

    Recorded coverage is lost, which is the honest outcome: the previous schema
    has nowhere to keep it, and it is rebuilt by running a league import again.
    Persisted game data is not involved either way.
    """
    op.drop_table(TABLE_NAME)
