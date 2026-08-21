"""HTTP routes for the web UI and health check.

Every route on this page reads the local database only. The MLB Stats API is
reached exclusively from the import CLI.
"""

from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BeforeValidator
from sqlalchemy.orm import Session

from app.analytics.league_hitting import (
    build_league_hits_context,
    compare_team_hits_to_league,
    supports_league_wide_average,
)
from app.analytics.league_strikeouts import (
    MissingLeagueStrikeoutDataError,
    build_league_strikeouts_context,
    compare_team_strikeouts_to_league,
    supports_league_wide_strikeout_average,
)
from app.analytics.team_hitting import DEFAULT_ROLLING_WINDOW, build_team_hits_analysis
from app.analytics.team_strikeouts import (
    MissingStrikeoutDataError,
    build_team_strikeouts_analysis,
)
from app.config import Settings
from app.database.repositories import (
    MIGRATION_HINT,
    DatabaseSchemaMissingError,
    get_league_season_ingestion,
    list_available_team_seasons,
    list_league_season,
    list_team_season,
)
from app.schemas.analytics import (
    TeamHitsAnalysis,
    TeamHitsLeagueComparison,
    TeamStrikeoutsAnalysis,
    TeamStrikeoutsLeagueComparison,
)
from app.web.charts import (
    STRIKEOUTS_CHART_DIV_ID,
    build_team_hits_figure,
    build_team_strikeouts_figure,
    plotly_bundle_javascript,
    render_figure_html,
    rolling_average_trace_name,
)
from app.web.dependencies import get_db_session
from app.web.formatting import (
    build_strikeout_summary_cards,
    build_summary_cards,
    format_league_comparison_note,
    format_league_strikeouts_backfill_note,
    format_league_strikeouts_note,
    format_long_date,
)
from app.web.navigation import HITS_PATH, STRIKEOUTS_PATH, build_nav_links
from app.web.selection import (
    build_team_options,
    build_team_seasons_catalog,
    select_season,
    select_team,
)

RollingWindow = Literal[5, 10, 15, 30]
ROLLING_WINDOW_OPTIONS: tuple[int, ...] = get_args(RollingWindow)


def _coerce_window(value: object) -> object:
    """Turn the query string into an int so the allowed values can be checked.

    A value that is not a number is passed through untouched so the reader sees
    the list of allowed windows rather than a parsing complaint.
    """
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    return value


RollingWindowParam = Annotated[RollingWindow, BeforeValidator(_coerce_window)]

PLOTLY_BUNDLE_PATH = "/vendor/plotly.min.js"
# Club and league marks are fetched by the browser from MLB's public logo
# host, keyed by the same team ids the application already stores. They are
# decorative: every page states the team in text as well, and the layout holds
# when the images do not load, which is what happens with no internet access.
MLB_LOGO_URL = "https://www.mlbstatic.com/team-logos/league-on-dark/1.svg"
TEAM_LOGO_URL_PREFIX = "https://www.mlbstatic.com/team-logos/"
IMPORT_COMMAND = (
    "poetry run python scripts/import_team_season.py --team-id 136 --season 2025"
)


def import_command_for(team_id: int, season: int) -> str:
    """Spell out the import command for the team-season actually selected."""
    return (
        f"poetry run python scripts/import_team_season.py "
        f"--team-id {team_id} --season {season}"
    )


def league_import_command_for(season: int) -> str:
    """Spell out the league-wide import command for the season selected."""
    return f"poetry run python scripts/import_league_season.py --season {season}"


def create_router(templates: Jinja2Templates, settings: Settings) -> APIRouter:
    """Build the application router with template and settings dependencies."""
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        session: Annotated[Session, Depends(get_db_session)],
        team_id: Annotated[
            int | None,
            Query(gt=0, description="MLB team id that has been imported locally."),
        ] = None,
        season: Annotated[
            int | None,
            Query(gt=0, description="Season that has been imported for the team."),
        ] = None,
        window: Annotated[
            RollingWindowParam,
            Query(description="Games in the trailing rolling average."),
        ] = DEFAULT_ROLLING_WINDOW,
    ) -> Response:
        """Render team hitting trends for one persisted team-season."""
        try:
            available = list_available_team_seasons(session)
        except DatabaseSchemaMissingError as exc:
            return _render_schema_error(templates, request, settings, exc)

        teams = build_team_options(available)
        context: dict[str, Any] = {
            "app_name": settings.app_name,
            "teams": teams,
            "team_seasons_catalog": build_team_seasons_catalog(teams),
            "window_options": ROLLING_WINDOW_OPTIONS,
            "selected_window": window,
            "selected_team": None,
            "selected_season": None,
            "import_command": IMPORT_COMMAND,
            "plotly_bundle_path": PLOTLY_BUNDLE_PATH,
            "mlb_logo_url": MLB_LOGO_URL,
            "team_logo_url_prefix": TEAM_LOGO_URL_PREFIX,
            "form_action": HITS_PATH,
            "nav_links": build_nav_links(
                current_path=HITS_PATH,
                team_id=team_id,
                season=season,
                window=window,
            ),
        }

        if not teams:
            context["state"] = "empty"
            return templates.TemplateResponse(
                request=request, name="index.html", context=context
            )

        selected_team = select_team(teams, team_id)
        if selected_team is None:
            context["state"] = "not_found"
            context["not_found_message"] = (
                f"No games are stored for team id {team_id}. "
                "Pick a team that has been imported, or import that team."
            )
            return templates.TemplateResponse(
                request=request, name="index.html", context=context, status_code=404
            )

        context["selected_team"] = selected_team
        selected_season = select_season(selected_team, season)
        if selected_season is None:
            context["state"] = "not_found"
            context["not_found_message"] = (
                f"No {season} games are stored for {selected_team.team_name}. "
                f"Stored seasons: "
                f"{', '.join(str(value) for value in selected_team.seasons)}."
            )
            return templates.TemplateResponse(
                request=request, name="index.html", context=context, status_code=404
            )

        context["selected_season"] = selected_season
        context["nav_links"] = build_nav_links(
            current_path=HITS_PATH,
            team_id=selected_team.team_id,
            season=selected_season,
            window=window,
        )
        games = list_team_season(
            session, team_id=selected_team.team_id, season=selected_season
        )
        analysis = build_team_hits_analysis(games, rolling_window=window)
        league_comparison = _load_league_comparison(session, analysis)
        figure = build_team_hits_figure(analysis, league_comparison)

        context.update(
            {
                "state": "ok",
                "analysis": analysis,
                "chart_html": render_figure_html(figure),
                "rolling_average_label": rolling_average_trace_name(window),
                "summary_cards": build_summary_cards(analysis, league_comparison),
                "league_comparison": league_comparison,
                "league_comparison_note": format_league_comparison_note(
                    league_comparison
                ),
                "data_through": format_long_date(analysis.last_game_date),
            }
        )
        return templates.TemplateResponse(
            request=request, name="index.html", context=context
        )

    @router.get(STRIKEOUTS_PATH, response_class=HTMLResponse)
    def strikeouts(
        request: Request,
        session: Annotated[Session, Depends(get_db_session)],
        team_id: Annotated[
            int | None,
            Query(gt=0, description="MLB team id that has been imported locally."),
        ] = None,
        season: Annotated[
            int | None,
            Query(gt=0, description="Season that has been imported for the team."),
        ] = None,
        window: Annotated[
            RollingWindowParam,
            Query(description="Games in the trailing rolling average."),
        ] = DEFAULT_ROLLING_WINDOW,
    ) -> Response:
        """Render batting strikeout trends for one persisted team-season."""
        try:
            available = list_available_team_seasons(session)
        except DatabaseSchemaMissingError as exc:
            return _render_schema_error(templates, request, settings, exc)

        teams = build_team_options(available)
        context: dict[str, Any] = {
            "app_name": settings.app_name,
            "teams": teams,
            "team_seasons_catalog": build_team_seasons_catalog(teams),
            "window_options": ROLLING_WINDOW_OPTIONS,
            "selected_window": window,
            "selected_team": None,
            "selected_season": None,
            "import_command": IMPORT_COMMAND,
            "plotly_bundle_path": PLOTLY_BUNDLE_PATH,
            "mlb_logo_url": MLB_LOGO_URL,
            "team_logo_url_prefix": TEAM_LOGO_URL_PREFIX,
            "form_action": STRIKEOUTS_PATH,
            "nav_links": build_nav_links(
                current_path=STRIKEOUTS_PATH,
                team_id=team_id,
                season=season,
                window=window,
            ),
        }

        if not teams:
            context["state"] = "empty"
            return templates.TemplateResponse(
                request=request, name="strikeouts.html", context=context
            )

        selected_team = select_team(teams, team_id)
        if selected_team is None:
            context["state"] = "not_found"
            context["not_found_message"] = (
                f"No games are stored for team id {team_id}. "
                "Pick a team that has been imported, or import that team."
            )
            return templates.TemplateResponse(
                request=request,
                name="strikeouts.html",
                context=context,
                status_code=404,
            )

        context["selected_team"] = selected_team
        selected_season = select_season(selected_team, season)
        if selected_season is None:
            context["state"] = "not_found"
            context["not_found_message"] = (
                f"No {season} games are stored for {selected_team.team_name}. "
                f"Stored seasons: "
                f"{', '.join(str(value) for value in selected_team.seasons)}."
            )
            return templates.TemplateResponse(
                request=request,
                name="strikeouts.html",
                context=context,
                status_code=404,
            )

        context["selected_season"] = selected_season
        context["nav_links"] = build_nav_links(
            current_path=STRIKEOUTS_PATH,
            team_id=selected_team.team_id,
            season=selected_season,
            window=window,
        )
        games = list_team_season(
            session, team_id=selected_team.team_id, season=selected_season
        )

        try:
            analysis = build_team_strikeouts_analysis(games, rolling_window=window)
        except MissingStrikeoutDataError as exc:
            # Stored before batting strikeouts were persisted. Charting these
            # games would mean inventing totals or quietly analysing a subset,
            # so the page asks for a re-import instead.
            context["state"] = "missing_strikeouts"
            context["missing_message"] = str(exc)
            context["games_missing"] = exc.games_missing
            context["games_total"] = exc.games_total
            context["reimport_command"] = import_command_for(
                selected_team.team_id, selected_season
            )
            return templates.TemplateResponse(
                request=request,
                name="strikeouts.html",
                context=context,
                status_code=409,
            )

        try:
            league_comparison = _load_league_strikeouts_comparison(session, analysis)
            league_note = format_league_strikeouts_note(league_comparison)
        except MissingLeagueStrikeoutDataError as exc:
            # Coverage is complete, but some stored league rows predate batting
            # strikeouts being persisted. The selected team's own page still
            # works; only the MLB-wide claim is withheld.
            league_comparison = None
            league_note = format_league_strikeouts_backfill_note(
                season=exc.season,
                records_missing=exc.records_missing,
                records_total=exc.records_total,
                reimport_command=league_import_command_for(selected_season),
            )

        figure = build_team_strikeouts_figure(analysis, league_comparison)
        context.update(
            {
                "state": "ok",
                "analysis": analysis,
                "chart_html": render_figure_html(
                    figure, div_id=STRIKEOUTS_CHART_DIV_ID
                ),
                "rolling_average_label": rolling_average_trace_name(window),
                "summary_cards": build_strikeout_summary_cards(
                    analysis, league_comparison
                ),
                "league_comparison": league_comparison,
                "league_comparison_note": league_note,
                "data_through": format_long_date(analysis.last_game_date),
            }
        )
        return templates.TemplateResponse(
            request=request, name="strikeouts.html", context=context
        )

    @router.get(PLOTLY_BUNDLE_PATH, include_in_schema=False)
    def plotly_bundle() -> Response:
        """Serve the plotly.js bundle from the installed package.

        Vendoring it at request time keeps a multi-megabyte file out of the
        repository while letting the page render without internet access.
        """
        return Response(
            content=plotly_bundle_javascript(),
            media_type="text/javascript",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @router.get("/health", response_class=JSONResponse)
    async def health() -> dict[str, str]:
        """Return a simple health-check payload."""
        return {
            "status": "ok",
            "app": settings.app_name,
        }

    return router


def _load_league_comparison(
    session: Session,
    analysis: TeamHitsAnalysis,
) -> TeamHitsLeagueComparison | None:
    """Read MLB context for the analysed season, or None when it is not earned.

    The completeness rule lives in ``app.analytics.league_hitting`` and the
    formula lives there too; this only wires the persisted coverage state and
    the persisted season to them. A season without complete coverage simply
    yields None, and the team's own page renders exactly as it did before.

    The season query cannot come back empty here: the analysis was built from
    games stored for this season, so those rows are part of what it returns.
    """
    coverage = get_league_season_ingestion(session, season=analysis.season)
    if not supports_league_wide_average(coverage):
        return None

    league_games = list_league_season(session, season=analysis.season)
    league = build_league_hits_context(league_games)
    return compare_team_hits_to_league(analysis, league)


def _load_league_strikeouts_comparison(
    session: Session,
    analysis: TeamStrikeoutsAnalysis,
) -> TeamStrikeoutsLeagueComparison | None:
    """Read MLB batting strikeout context, or None when it is not earned.

    Two conditions must hold, and both rules live in
    ``app.analytics.league_strikeouts``: the season's latest league-wide
    refresh reached ``COMPLETE`` coverage, and every stored record for the
    season carries a known batting strikeout total. This only wires the
    persisted coverage state and the persisted season to them.

    A season without complete coverage yields None. A season whose stored rows
    include an unknown strikeout total raises
    ``MissingLeagueStrikeoutDataError``, which the route turns into backfill
    guidance rather than a missing-coverage message, because the two are
    different problems with different remedies.

    The season query cannot come back empty here: the analysis was built from
    games stored for this season, so those rows are part of what it returns.
    """
    coverage = get_league_season_ingestion(session, season=analysis.season)
    if not supports_league_wide_strikeout_average(coverage):
        return None

    league_games = list_league_season(session, season=analysis.season)
    league = build_league_strikeouts_context(league_games)
    return compare_team_strikeouts_to_league(analysis, league)


def _render_schema_error(
    templates: Jinja2Templates,
    request: Request,
    settings: Settings,
    error: DatabaseSchemaMissingError,
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "app_name": settings.app_name,
            "mlb_logo_url": MLB_LOGO_URL,
            "heading": "The database schema is not ready",
            "message": str(error),
            "commands": [MIGRATION_HINT],
        },
        status_code=503,
    )
