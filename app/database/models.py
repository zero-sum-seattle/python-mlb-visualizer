"""ORM models for persisted team game lines and league ingestion state."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.schemas.games import TeamGameBattingLine, TeamGamePitchingLine
from app.schemas.ingestion import (
    LeagueSeasonIngestionState,
    LeagueSeasonIngestionStatus,
)


class TeamGameBattingLineRecord(Base):
    """Persistence representation of one team's batting line in one completed game."""

    __tablename__ = "team_game_batting_lines"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "game_pk",
            name="uq_team_game_batting_lines_team_id_game_pk",
        ),
        CheckConstraint("game_pk > 0", name="game_pk_positive"),
        CheckConstraint("season > 0", name="season_positive"),
        CheckConstraint("team_id > 0", name="team_id_positive"),
        CheckConstraint("opponent_id > 0", name="opponent_id_positive"),
        CheckConstraint("hits >= 0", name="hits_nonnegative"),
        CheckConstraint("runs >= 0", name="runs_nonnegative"),
        CheckConstraint(
            "strikeouts IS NULL OR strikeouts >= 0",
            name="strikeouts_nonnegative_or_unknown",
        ),
        CheckConstraint(
            "base_on_balls IS NULL OR base_on_balls >= 0",
            name="base_on_balls_nonnegative_or_unknown",
        ),
        CheckConstraint(
            "hit_by_pitch IS NULL OR hit_by_pitch >= 0",
            name="hit_by_pitch_nonnegative_or_unknown",
        ),
        CheckConstraint("game_number >= 1", name="game_number_min"),
        CheckConstraint("scheduled_innings >= 1", name="scheduled_innings_min"),
        CheckConstraint(
            "home_away IN ('home', 'away')",
            name="home_away_valid",
        ),
        Index(
            "ix_team_game_batting_lines_team_season_order",
            "team_id",
            "season",
            "game_date",
            "game_number",
            "game_pk",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_pk: Mapped[int] = mapped_column(Integer, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    team_name: Mapped[str] = mapped_column(String, nullable=False)
    opponent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    opponent_name: Mapped[str] = mapped_column(String, nullable=False)
    home_away: Mapped[str] = mapped_column(String, nullable=False)
    hits: Mapped[int] = mapped_column(Integer, nullable=False)
    runs: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable on purpose: rows persisted before batting strikeouts were
    # collected have an unknown total, which is not the same as zero. A
    # re-import replaces the NULL with the real MLB value.
    strikeouts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Nullable for the same reason as strikeouts: rows persisted before these
    # two columns existed have an unknown total, not a zero one.
    base_on_balls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hit_by_pitch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    game_number: Mapped[int] = mapped_column(Integer, nullable=False)
    doubleheader: Mapped[bool] = mapped_column(Boolean, nullable=False)
    scheduled_innings: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )

    def to_domain(self) -> TeamGameBattingLine:
        """Convert this row to the normalized Pydantic domain model."""
        return TeamGameBattingLine(
            game_pk=self.game_pk,
            game_date=self.game_date,
            season=self.season,
            team_id=self.team_id,
            team_name=self.team_name,
            opponent_id=self.opponent_id,
            opponent_name=self.opponent_name,
            home_away=self.home_away,
            hits=self.hits,
            runs=self.runs,
            strikeouts=self.strikeouts,
            base_on_balls=self.base_on_balls,
            hit_by_pitch=self.hit_by_pitch,
            status=self.status,
            game_number=self.game_number,
            doubleheader=self.doubleheader,
            scheduled_innings=self.scheduled_innings,
        )

    def apply_domain(self, line: TeamGameBattingLine) -> None:
        """Copy persisted baseball fields from a domain record onto this row."""
        self.game_date = line.game_date
        self.season = line.season
        self.team_name = line.team_name
        self.opponent_id = line.opponent_id
        self.opponent_name = line.opponent_name
        self.home_away = line.home_away
        self.hits = line.hits
        self.runs = line.runs
        self.strikeouts = line.strikeouts
        self.base_on_balls = line.base_on_balls
        self.hit_by_pitch = line.hit_by_pitch
        self.status = line.status
        self.game_number = line.game_number
        self.doubleheader = line.doubleheader
        self.scheduled_innings = line.scheduled_innings

    @staticmethod
    def from_domain(
        line: TeamGameBattingLine,
        *,
        created_at: datetime,
        updated_at: datetime,
    ) -> TeamGameBattingLineRecord:
        """Build a new ORM row from a domain record and timestamps."""
        return TeamGameBattingLineRecord(
            game_pk=line.game_pk,
            game_date=line.game_date,
            season=line.season,
            team_id=line.team_id,
            team_name=line.team_name,
            opponent_id=line.opponent_id,
            opponent_name=line.opponent_name,
            home_away=line.home_away,
            hits=line.hits,
            runs=line.runs,
            strikeouts=line.strikeouts,
            base_on_balls=line.base_on_balls,
            hit_by_pitch=line.hit_by_pitch,
            status=line.status,
            game_number=line.game_number,
            doubleheader=line.doubleheader,
            scheduled_innings=line.scheduled_innings,
            created_at=created_at,
            updated_at=updated_at,
        )


class TeamGamePitchingLineRecord(Base):
    """Persistence representation of one team's pitching line in one completed game.

    A separate table from the batting line rather than more columns on it. The
    two are different stat groups fetched in different MLB requests, and half
    of each one's columns would be meaningless on the other row. Keeping them
    apart also means a season imported before pitching existed simply has no
    pitching rows, rather than a batting row full of nulls.

    ``outs`` is the innings column. See ``TeamGamePitchingLine`` for why innings
    pitched is never stored as a number.
    """

    __tablename__ = "team_game_pitching_lines"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "game_pk",
            name="uq_team_game_pitching_lines_team_id_game_pk",
        ),
        CheckConstraint("game_pk > 0", name="pitching_game_pk_positive"),
        CheckConstraint("season > 0", name="pitching_season_positive"),
        CheckConstraint("team_id > 0", name="pitching_team_id_positive"),
        CheckConstraint("opponent_id > 0", name="pitching_opponent_id_positive"),
        CheckConstraint("outs >= 0", name="outs_nonnegative"),
        CheckConstraint("hits_allowed >= 0", name="hits_allowed_nonnegative"),
        CheckConstraint("runs_allowed >= 0", name="runs_allowed_nonnegative"),
        CheckConstraint("earned_runs >= 0", name="earned_runs_nonnegative"),
        CheckConstraint(
            "pitching_base_on_balls >= 0", name="pitching_base_on_balls_nonnegative"
        ),
        CheckConstraint(
            "pitching_strikeouts >= 0", name="pitching_strikeouts_nonnegative"
        ),
        CheckConstraint("home_runs_allowed >= 0", name="home_runs_allowed_nonnegative"),
        CheckConstraint("batters_faced >= 0", name="batters_faced_nonnegative"),
        # Definitional, not empirical: an earned run is a run, a home run is a
        # hit, and every out is recorded against a batter faced.
        CheckConstraint(
            "earned_runs <= runs_allowed", name="earned_runs_within_runs_allowed"
        ),
        CheckConstraint(
            "home_runs_allowed <= hits_allowed",
            name="home_runs_within_hits_allowed",
        ),
        CheckConstraint("batters_faced >= outs", name="batters_faced_covers_outs"),
        CheckConstraint("number_of_pitches >= 0", name="number_of_pitches_nonnegative"),
        CheckConstraint("strikes >= 0", name="strikes_nonnegative"),
        CheckConstraint(
            "strikes <= number_of_pitches", name="strikes_within_number_of_pitches"
        ),
        CheckConstraint("game_number >= 1", name="pitching_game_number_min"),
        CheckConstraint(
            "scheduled_innings >= 1", name="pitching_scheduled_innings_min"
        ),
        CheckConstraint(
            "home_away IN ('home', 'away')",
            name="pitching_home_away_valid",
        ),
        Index(
            "ix_team_game_pitching_lines_team_season_order",
            "team_id",
            "season",
            "game_date",
            "game_number",
            "game_pk",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_pk: Mapped[int] = mapped_column(Integer, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    team_name: Mapped[str] = mapped_column(String, nullable=False)
    opponent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    opponent_name: Mapped[str] = mapped_column(String, nullable=False)
    home_away: Mapped[str] = mapped_column(String, nullable=False)
    outs: Mapped[int] = mapped_column(Integer, nullable=False)
    hits_allowed: Mapped[int] = mapped_column(Integer, nullable=False)
    runs_allowed: Mapped[int] = mapped_column(Integer, nullable=False)
    earned_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    # Prefixed to keep the pitching sense unmistakable beside the batting
    # table's identically named columns, which count the opposite thing.
    pitching_base_on_balls: Mapped[int] = mapped_column(Integer, nullable=False)
    pitching_strikeouts: Mapped[int] = mapped_column(Integer, nullable=False)
    home_runs_allowed: Mapped[int] = mapped_column(Integer, nullable=False)
    batters_faced: Mapped[int] = mapped_column(Integer, nullable=False)
    number_of_pitches: Mapped[int] = mapped_column(Integer, nullable=False)
    # Balls are not stored: MLB leaves that field empty on the team game log,
    # and it is number_of_pitches - strikes.
    strikes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    game_number: Mapped[int] = mapped_column(Integer, nullable=False)
    doubleheader: Mapped[bool] = mapped_column(Boolean, nullable=False)
    scheduled_innings: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )

    def to_domain(self) -> TeamGamePitchingLine:
        """Convert this row to the normalized Pydantic domain model."""
        return TeamGamePitchingLine(
            game_pk=self.game_pk,
            game_date=self.game_date,
            season=self.season,
            team_id=self.team_id,
            team_name=self.team_name,
            opponent_id=self.opponent_id,
            opponent_name=self.opponent_name,
            home_away=self.home_away,
            outs=self.outs,
            hits_allowed=self.hits_allowed,
            runs_allowed=self.runs_allowed,
            earned_runs=self.earned_runs,
            base_on_balls=self.pitching_base_on_balls,
            strikeouts=self.pitching_strikeouts,
            home_runs_allowed=self.home_runs_allowed,
            batters_faced=self.batters_faced,
            number_of_pitches=self.number_of_pitches,
            strikes=self.strikes,
            status=self.status,
            game_number=self.game_number,
            doubleheader=self.doubleheader,
            scheduled_innings=self.scheduled_innings,
        )

    def apply_domain(self, line: TeamGamePitchingLine) -> None:
        """Copy persisted baseball fields from a domain record onto this row."""
        self.game_date = line.game_date
        self.season = line.season
        self.team_name = line.team_name
        self.opponent_id = line.opponent_id
        self.opponent_name = line.opponent_name
        self.home_away = line.home_away
        self.outs = line.outs
        self.hits_allowed = line.hits_allowed
        self.runs_allowed = line.runs_allowed
        self.earned_runs = line.earned_runs
        self.pitching_base_on_balls = line.base_on_balls
        self.pitching_strikeouts = line.strikeouts
        self.home_runs_allowed = line.home_runs_allowed
        self.batters_faced = line.batters_faced
        self.number_of_pitches = line.number_of_pitches
        self.strikes = line.strikes
        self.status = line.status
        self.game_number = line.game_number
        self.doubleheader = line.doubleheader
        self.scheduled_innings = line.scheduled_innings

    @staticmethod
    def from_domain(
        line: TeamGamePitchingLine,
        *,
        created_at: datetime,
        updated_at: datetime,
    ) -> TeamGamePitchingLineRecord:
        """Build a new ORM row from a domain record and timestamps."""
        return TeamGamePitchingLineRecord(
            game_pk=line.game_pk,
            game_date=line.game_date,
            season=line.season,
            team_id=line.team_id,
            team_name=line.team_name,
            opponent_id=line.opponent_id,
            opponent_name=line.opponent_name,
            home_away=line.home_away,
            outs=line.outs,
            hits_allowed=line.hits_allowed,
            runs_allowed=line.runs_allowed,
            earned_runs=line.earned_runs,
            pitching_base_on_balls=line.base_on_balls,
            pitching_strikeouts=line.strikeouts,
            home_runs_allowed=line.home_runs_allowed,
            batters_faced=line.batters_faced,
            number_of_pitches=line.number_of_pitches,
            strikes=line.strikes,
            status=line.status,
            game_number=line.game_number,
            doubleheader=line.doubleheader,
            scheduled_innings=line.scheduled_innings,
            created_at=created_at,
            updated_at=updated_at,
        )


class LeagueSeasonIngestionRecord(Base):
    """Coverage state of the most recent league-wide ingestion of one season.

    One row per season, holding current state rather than an attempt log. That
    is the smallest model that answers the question Milestone 4 exists for:

        did the most recent league-wide ingestion of this season successfully
        cover every MLB team discovered for it?

    A rerun overwrites the row, which is what makes an incomplete run
    retryable. Import history and per-attempt auditing are deliberately absent;
    nothing in the application needs them yet, and adding them would mean
    maintaining a growing table with no reader.

    The CHECK constraints exist so a partial import cannot be labelled complete
    even if application code is later changed carelessly: ``COMPLETE`` is
    rejected by the database unless at least one team was expected and none
    failed.
    """

    __tablename__ = "league_season_ingestions"
    __table_args__ = (
        UniqueConstraint("season", name="uq_league_season_ingestions_season"),
        CheckConstraint("season > 0", name="season_positive"),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'INCOMPLETE')",
            name="status_valid",
        ),
        CheckConstraint("expected_team_count >= 0", name="expected_team_count_min"),
        CheckConstraint("successful_team_count >= 0", name="successful_team_count_min"),
        CheckConstraint("failed_team_count >= 0", name="failed_team_count_min"),
        CheckConstraint(
            "(status = 'RUNNING') = (completed_at IS NULL)",
            name="completed_at_matches_status",
        ),
        CheckConstraint(
            "status = 'RUNNING' OR "
            "successful_team_count + failed_team_count = expected_team_count",
            name="finished_counts_add_up",
        ),
        CheckConstraint(
            "status <> 'COMPLETE' OR "
            "(failed_team_count = 0 AND expected_team_count > 0)",
            name="complete_requires_full_coverage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    expected_team_count: Mapped[int] = mapped_column(Integer, nullable=False)
    successful_team_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_team_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    # Unset while a run is in flight. A row still holding NULL after the process
    # exited means that run never finished, which is not the same as a run that
    # finished with failures.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    def to_domain(self) -> LeagueSeasonIngestionState:
        """Convert this row to the normalized Pydantic domain model."""
        return LeagueSeasonIngestionState(
            season=self.season,
            status=LeagueSeasonIngestionStatus(self.status),
            expected_team_count=self.expected_team_count,
            successful_team_count=self.successful_team_count,
            failed_team_count=self.failed_team_count,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )

    def apply_domain(self, state: LeagueSeasonIngestionState) -> None:
        """Copy coverage state from a domain record onto this row."""
        self.season = state.season
        self.status = state.status.value
        self.expected_team_count = state.expected_team_count
        self.successful_team_count = state.successful_team_count
        self.failed_team_count = state.failed_team_count
        self.started_at = state.started_at
        self.completed_at = state.completed_at

    @staticmethod
    def from_domain(state: LeagueSeasonIngestionState) -> LeagueSeasonIngestionRecord:
        """Build a new ORM row from a domain coverage state."""
        record = LeagueSeasonIngestionRecord()
        record.apply_domain(state)
        return record
