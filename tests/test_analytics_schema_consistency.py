"""Characterize shared consistency rules through the public analytics models."""

import pytest
from pydantic import BaseModel, ValidationError

from app.analytics.team_baserunners import build_team_baserunners_analysis
from app.analytics.team_hits_allowed import build_team_hits_allowed_analysis
from app.analytics.team_hitting import build_team_hits_analysis
from app.analytics.team_hitting_comparison import build_team_hitting_comparison_analysis
from app.analytics.team_pitching import build_team_pitching_analysis
from app.analytics.team_run_differential import build_team_run_differential_analysis
from app.analytics.team_runs import build_team_runs_analysis
from app.analytics.team_strikeouts import build_team_strikeouts_analysis
from app.schemas.analytics import (
    LeaguePitchingContext,
    TeamBaserunnersLeagueComparison,
    TeamHitsAllowedLeagueComparison,
    TeamHitsLeagueComparison,
    TeamPitchingLeagueComparison,
    TeamRunsLeagueComparison,
    TeamStrikeoutsLeagueComparison,
)
from tests.factories import (
    make_batting_line,
    make_league_baserunners_context,
    make_league_hits_context,
    make_league_runs_context,
    make_league_strikeouts_context,
    make_pitching_line,
    make_run_result,
)

# Builders supply valid starting objects; each test revalidates a modified dump
# through the public schema rather than using model_copy (which skips validation).
_batting = [make_batting_line(strikeouts=9, base_on_balls=2, hit_by_pitch=0)]
_pitching = [make_pitching_line()]
_hits = build_team_hits_analysis(_batting)
_strikeouts = build_team_strikeouts_analysis(_batting)
_analyses = [
    _hits,
    _strikeouts,
    build_team_runs_analysis(_batting),
    build_team_baserunners_analysis(_batting),
    build_team_pitching_analysis(_pitching),
    build_team_run_differential_analysis([make_run_result()]),
    build_team_hits_allowed_analysis(_pitching),
    build_team_hitting_comparison_analysis(
        _hits,
        _strikeouts,
        make_league_hits_context(),
        make_league_strikeouts_context(),
    ),
]


@pytest.mark.parametrize("analysis", _analyses, ids=lambda model: type(model).__name__)
@pytest.mark.parametrize("point_copies", [1, 2])
def test_summary_game_count_matches_points(
    analysis: BaseModel, point_copies: int
) -> None:
    data = analysis.model_dump()
    data["points"] = data["points"] * point_copies
    if point_copies == 1:
        assert type(analysis).model_validate(data) == analysis
    else:
        with pytest.raises(ValidationError) as caught:
            type(analysis).model_validate(data)
        assert caught.value.errors()[0]["msg"] == (
            "Value error, summary.games_played must equal the number of chart points"
        )


@pytest.mark.parametrize(
    "summary",
    [analysis.summary for analysis in _analyses[:-1]],
    ids=lambda model: type(model).__name__,
)
@pytest.mark.parametrize(
    ("prior", "change"), [(None, None), (0.0, 0.0), (0.0, None), (None, 0.0)]
)
def test_prior_window_fields_are_present_or_absent_together(
    summary: BaseModel, prior: float | None, change: float | None
) -> None:
    data = summary.model_dump()
    prior_field = (
        "prior_window_era" if "prior_window_era" in data else "prior_window_average"
    )
    data[prior_field] = prior
    data["change_vs_prior_window"] = change
    if (prior is None) == (change is None):
        validated = type(summary).model_validate(data).model_dump()
        assert validated[prior_field] == prior
        assert validated["change_vs_prior_window"] == change
    else:
        with pytest.raises(ValidationError) as caught:
            type(summary).model_validate(data)
        assert caught.value.errors()[0]["msg"] == (
            f"Value error, {prior_field} and change_vs_prior_window must both be "
            "present or both be None"
        )


_league_pitching = LeaguePitchingContext(
    season=2025,
    teams_represented=2,
    team_game_records=10,
    outs=270,
    innings_pitched=90.0,
    total_earned_runs=30,
    era=3.0,
    whip=1.0,
    strikeouts_per_nine=9.0,
    walks_per_nine=2.0,
)
_leagues = [
    make_league_hits_context(),
    make_league_strikeouts_context(),
    make_league_runs_context(),
    make_league_baserunners_context(),
    _league_pitching,
]


@pytest.mark.parametrize("league", _leagues, ids=lambda model: type(model).__name__)
@pytest.mark.parametrize("teams", [1, 10, 11])
def test_represented_teams_cannot_exceed_records(league: BaseModel, teams: int) -> None:
    data = league.model_dump()
    data["teams_represented"] = teams
    if teams <= 10:
        assert (
            type(league).model_validate(data).model_dump()["teams_represented"] == teams
        )
    else:
        with pytest.raises(ValidationError) as caught:
            type(league).model_validate(data)
        assert caught.value.errors()[0]["msg"] == (
            "Value error, teams_represented (11) cannot exceed team_game_records (10)"
        )


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (
            TeamHitsLeagueComparison,
            dict(
                team_hits_per_game=8.0,
                league=make_league_hits_context(),
                difference_vs_mlb=0.0,
            ),
        ),
        (
            TeamStrikeoutsLeagueComparison,
            dict(
                team_strikeouts_per_game=8.0,
                league=make_league_strikeouts_context(),
                difference_vs_mlb=0.0,
            ),
        ),
        (
            TeamRunsLeagueComparison,
            dict(
                team_runs_per_game=4.5,
                league=make_league_runs_context(),
                difference_vs_mlb=0.0,
            ),
        ),
        (
            TeamBaserunnersLeagueComparison,
            dict(
                team_baserunners_per_game=10.0,
                league=make_league_baserunners_context(),
                difference_vs_mlb=0.0,
            ),
        ),
        (
            TeamHitsAllowedLeagueComparison,
            dict(
                team_hits_allowed_per_game=8.0,
                league=make_league_hits_context(),
                difference_vs_mlb=0.0,
            ),
        ),
        (
            TeamPitchingLeagueComparison,
            dict(
                team_era=3.0,
                team_whip=1.0,
                team_strikeouts_per_nine=9.0,
                team_walks_per_nine=2.0,
                league=_league_pitching,
                era_difference_vs_mlb=0.0,
                whip_difference_vs_mlb=0.0,
            ),
        ),
    ],
    ids=lambda value: value.__name__ if isinstance(value, type) else None,
)
@pytest.mark.parametrize("season", [2025, 2026])
def test_comparison_requires_the_league_season(
    model: type[BaseModel], fields: dict[str, object], season: int
) -> None:
    data = dict(fields, team_id=136, team_name="Seattle Mariners", season=season)
    if season == 2025:
        assert model.model_validate(data).model_dump()["season"] == season
    else:
        with pytest.raises(ValidationError) as caught:
            model.model_validate(data)
        assert caught.value.errors()[0]["msg"] == (
            "Value error, season (2026) must match the league context season (2025)"
        )
