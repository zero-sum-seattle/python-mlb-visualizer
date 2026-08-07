"""Schemas describing what is currently persisted in the local database."""

from pydantic import BaseModel, ConfigDict, Field


class AvailableTeamSeason(BaseModel):
    """One team-season that has games stored locally.

    ``team_name`` is the historical name recorded during ingestion of that
    season, so a franchise that was renamed keeps the right name per season.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Name recorded for the season.")
    season: int = Field(gt=0, description="Season with persisted games.")
    games_played: int = Field(ge=1, description="Rows stored for the team-season.")
