"""Schemas describing MLB clubs as the upstream API reports them."""

from pydantic import BaseModel, ConfigDict, Field


class MlbTeam(BaseModel):
    """One Major League club that the MLB Stats API reports for a season.

    ``team_name`` is the name upstream returns for the requested season, so a
    franchise that has since been renamed or relocated keeps its contemporary
    name. This is the same convention persisted game rows already follow.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: int = Field(gt=0, description="MLB team id.")
    team_name: str = Field(min_length=1, description="Name reported for the season.")
    season: int = Field(gt=0, description="Season the team was discovered for.")
