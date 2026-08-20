"""Schemas for team-season and league-season ingestion and persistence results."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TeamGamePersistenceResult(BaseModel):
    """Counts from upserting a batch of team game batting lines."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inserted: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)


class TeamSeasonIngestionResult(BaseModel):
    """Outcome of ingesting one team-season of batting lines."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0)
    team_name: str = Field(min_length=1)
    season: int = Field(gt=0)
    fetched: int = Field(ge=0)
    inserted: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)

    @model_validator(mode="after")
    def _fetched_matches_counts(self) -> TeamSeasonIngestionResult:
        total = self.inserted + self.updated + self.unchanged
        if self.fetched != total:
            raise ValueError(
                f"fetched ({self.fetched}) must equal inserted + updated + "
                f"unchanged ({total})"
            )
        return self


class LeagueTeamIngestionStatus(StrEnum):
    """Outcome of one team-season attempt made inside a league-wide ingestion."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class LeagueSeasonIngestionStatus(StrEnum):
    """Coverage state of a league-wide ingestion of one season.

    These values describe **ingestion coverage** and nothing else.

    ``COMPLETE`` means every MLB team discovered for that season was
    successfully ingested by that run. It does not assert that the regular
    season has finished being played, and it must never be read as "this season
    is final". A league-wide ingestion of an in-progress season can legitimately
    reach ``COMPLETE`` while every team still has games left to play; what is
    complete is the refresh, not the season.

    ``RUNNING`` is a persisted marker only. A returned ingestion result is
    always finished, so it is never ``RUNNING``.
    """

    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class LeagueTeamIngestionResult(BaseModel):
    """Outcome of one team inside a league-wide ingestion.

    A failed team keeps its identity and its error message so a rerun, an
    operator, or a future scheduler can tell exactly which club is missing.
    Counts on a failed team are zero because nothing was persisted for it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0)
    team_name: str = Field(min_length=1)
    season: int = Field(gt=0)
    status: LeagueTeamIngestionStatus
    fetched: int = Field(ge=0)
    inserted: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    error: str | None = None

    @classmethod
    def from_team_result(cls, result: TeamSeasonIngestionResult) -> Self:
        """Wrap a successful team-season ingestion result."""
        return cls(
            team_id=result.team_id,
            team_name=result.team_name,
            season=result.season,
            status=LeagueTeamIngestionStatus.SUCCEEDED,
            fetched=result.fetched,
            inserted=result.inserted,
            updated=result.updated,
            unchanged=result.unchanged,
            error=None,
        )

    @classmethod
    def from_failure(
        cls,
        *,
        team_id: int,
        team_name: str,
        season: int,
        error: str,
    ) -> Self:
        """Record a team whose ingestion attempt did not persist anything."""
        return cls(
            team_id=team_id,
            team_name=team_name,
            season=season,
            status=LeagueTeamIngestionStatus.FAILED,
            fetched=0,
            inserted=0,
            updated=0,
            unchanged=0,
            error=error,
        )

    @model_validator(mode="after")
    def _status_matches_outcome(self) -> LeagueTeamIngestionResult:
        if self.status is LeagueTeamIngestionStatus.SUCCEEDED:
            if self.error is not None:
                raise ValueError("a succeeded team result must not carry an error")
            total = self.inserted + self.updated + self.unchanged
            if self.fetched != total:
                raise ValueError(
                    f"fetched ({self.fetched}) must equal inserted + updated + "
                    f"unchanged ({total})"
                )
            return self

        if not self.error:
            raise ValueError("a failed team result must carry an error message")
        if self.fetched or self.inserted or self.updated or self.unchanged:
            raise ValueError("a failed team result must have zero persistence counts")
        return self


class LeagueSeasonIngestionResult(BaseModel):
    """Outcome of one league-wide ingestion of a season.

    The aggregate counts are the sum of the per-team counts, and
    ``teams_discovered`` is the number of teams the run actually attempted.
    Coverage is therefore derived from what happened rather than inferred from
    how many rows ended up in the database.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    season: int = Field(gt=0)
    teams_discovered: int = Field(ge=1)
    teams_succeeded: int = Field(ge=0)
    teams_failed: int = Field(ge=0)
    team_game_records_fetched: int = Field(
        ge=0,
        description=(
            "Team-game batting records fetched, not MLB games. One MLB game "
            "produces two team-game records once both clubs are ingested."
        ),
    )
    inserted: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    status: LeagueSeasonIngestionStatus
    started_at: datetime
    completed_at: datetime
    team_results: tuple[LeagueTeamIngestionResult, ...]

    @model_validator(mode="after")
    def _counts_agree_with_team_results(self) -> LeagueSeasonIngestionResult:
        if self.status is LeagueSeasonIngestionStatus.RUNNING:
            raise ValueError("a returned league ingestion result is never RUNNING")

        if self.teams_discovered != self.teams_succeeded + self.teams_failed:
            raise ValueError(
                f"teams_discovered ({self.teams_discovered}) must equal "
                f"teams_succeeded + teams_failed "
                f"({self.teams_succeeded + self.teams_failed})"
            )
        if len(self.team_results) != self.teams_discovered:
            raise ValueError(
                f"team_results holds {len(self.team_results)} entries but "
                f"{self.teams_discovered} teams were discovered"
            )

        succeeded = sum(
            1
            for result in self.team_results
            if result.status is LeagueTeamIngestionStatus.SUCCEEDED
        )
        if succeeded != self.teams_succeeded:
            raise ValueError(
                f"teams_succeeded ({self.teams_succeeded}) does not match the "
                f"{succeeded} succeeded team results"
            )

        for field in ("fetched", "inserted", "updated", "unchanged"):
            expected = sum(getattr(result, field) for result in self.team_results)
            reported = getattr(
                self, "team_game_records_fetched" if field == "fetched" else field
            )
            if reported != expected:
                raise ValueError(
                    f"aggregate {field} ({reported}) does not match the "
                    f"{expected} summed from team results"
                )

        seasons = {result.season for result in self.team_results}
        if seasons != {self.season}:
            raise ValueError(
                f"team results cover seasons {sorted(seasons)} but the league "
                f"ingestion is for {self.season}"
            )

        team_ids = [result.team_id for result in self.team_results]
        if len(set(team_ids)) != len(team_ids):
            raise ValueError("team results must hold each team at most once")

        complete = self.status is LeagueSeasonIngestionStatus.COMPLETE
        if complete != (self.teams_failed == 0):
            raise ValueError(
                f"status {self.status} disagrees with {self.teams_failed} failed teams"
            )
        return self


class LeagueSeasonIngestionState(BaseModel):
    """Persisted coverage state of the most recent league ingestion of a season.

    ``completed_at`` is unset while a run is ``RUNNING``. A row left ``RUNNING``
    means the process that started it never finished, so its coverage is
    unknown and must not be trusted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    season: int = Field(gt=0)
    status: LeagueSeasonIngestionStatus
    expected_team_count: int = Field(ge=0)
    successful_team_count: int = Field(ge=0)
    failed_team_count: int = Field(ge=0)
    started_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _state_is_internally_consistent(self) -> LeagueSeasonIngestionState:
        running = self.status is LeagueSeasonIngestionStatus.RUNNING
        if running != (self.completed_at is None):
            raise ValueError(
                f"status {self.status} disagrees with completed_at "
                f"{self.completed_at!r}"
            )
        if running:
            return self

        attempted = self.successful_team_count + self.failed_team_count
        if attempted != self.expected_team_count:
            raise ValueError(
                f"expected_team_count ({self.expected_team_count}) must equal "
                f"successful + failed ({attempted})"
            )
        if self.status is LeagueSeasonIngestionStatus.COMPLETE and (
            self.failed_team_count != 0 or self.expected_team_count == 0
        ):
            raise ValueError(
                "COMPLETE coverage requires at least one team and no failures"
            )
        return self
