"""Schemas for team-season ingestion and persistence results."""

from __future__ import annotations

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
