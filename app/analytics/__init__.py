"""Baseball analytics calculated from normalized domain records."""

from app.analytics.team_hitting import (
    DEFAULT_ROLLING_WINDOW,
    TeamHitsAnalysisError,
    build_team_hits_analysis,
)
from app.analytics.team_runs import (
    TeamRunsAnalysisError,
    build_team_runs_analysis,
)
from app.analytics.team_strikeouts import (
    MissingStrikeoutDataError,
    TeamStrikeoutsAnalysisError,
    build_team_strikeouts_analysis,
)

__all__ = [
    "DEFAULT_ROLLING_WINDOW",
    "MissingStrikeoutDataError",
    "TeamHitsAnalysisError",
    "TeamRunsAnalysisError",
    "TeamStrikeoutsAnalysisError",
    "build_team_hits_analysis",
    "build_team_runs_analysis",
    "build_team_strikeouts_analysis",
]
