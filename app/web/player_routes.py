"""DB-only Player directory, selection, and season hitting overview."""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.analytics.player_hitting import (
    PlayerHittingAnalysisError,
    build_player_hitting_overview,
)
from app.config import Settings
from app.database.repositories import (
    MIGRATION_HINT,
    DatabaseSchemaMissingError,
    get_player_catalog_entry,
    get_player_season_hitting,
    list_player_catalog,
    list_player_catalog_seasons,
)
from app.schemas.players import PlayerSeasonCatalogEntry, normalize_player_name
from app.web.charts import (
    PLAYER_PA_RATES_CHART_DIV_ID,
    build_player_plate_appearance_rates_figure,
    render_figure_html,
)
from app.web.dependencies import get_db_session
from app.web.formatting import (
    build_player_hitting_rate_cards,
    build_player_hitting_total_cards,
)
from app.web.routes import MLB_LOGO_URL, PLOTLY_BUNDLE_PATH

# A fixed cap keeps broad searches readable without a pagination framework.
PLAYER_SEARCH_LIMIT = 50
PLAYER_CATALOG_IMPORT_COMMAND = (
    "poetry run python scripts/import_player_catalog.py --season {season}"
)
PLAYER_SEASON_IMPORT_COMMAND = (
    "poetry run python scripts/import_player_season.py "
    "--player-id {player_id} --season {season}"
)
PLAYER_DIRECTORY_PATH = "/players"
PLAYER_HITTING_PATH = "/players/hitting"


def player_hitting_url(*, season: int, player_id: int) -> str:
    """Return the shareable hitting overview URL for one Player-season."""
    return f"{PLAYER_HITTING_PATH}?" + urlencode(
        {"season": season, "player_id": player_id}
    )


def player_selection_url(*, season: int, player_id: int) -> str:
    """Return the directory URL with this Player-season already selected."""
    return f"{PLAYER_DIRECTORY_PATH}?" + urlencode(
        {"season": season, "player_id": player_id}
    )


def search_player_catalog(
    catalog: list[PlayerSeasonCatalogEntry], query: str
) -> list[PlayerSeasonCatalogEntry]:
    """Return catalog members whose name contains the query, keeping catalog order.

    Uses the catalog's own case and accent folding, so ``rodriguez`` finds
    ``Rodríguez``. An empty query matches nobody rather than everybody.
    """
    folded_query = normalize_player_name(query)
    if not folded_query:
        return []
    return [
        player
        for player in catalog
        if folded_query in normalize_player_name(player.full_name)
    ]


def create_player_router(templates: Jinja2Templates, settings: Settings) -> APIRouter:
    """Build the Player directory and the Player season hitting overview."""
    router = APIRouter()

    @router.get(PLAYER_DIRECTORY_PATH, response_class=HTMLResponse)
    def player_directory(
        request: Request,
        session: Annotated[Session, Depends(get_db_session)],
        season: Annotated[int | None, Query(gt=0)] = None,
        q: str = "",
        player_id: Annotated[int | None, Query(gt=0)] = None,
    ) -> HTMLResponse:
        seasons: list[int] = []
        catalog: list[PlayerSeasonCatalogEntry] = []
        message = None
        status = 200
        schema_missing = False
        try:
            seasons = list_player_catalog_seasons(session)
            if season is None and seasons:
                season = seasons[0]
            if season is not None and season in seasons:
                catalog = list_player_catalog(session, season=season)
            elif season is not None:
                message = f"No Player catalog is stored locally for {season}."
                status = 404
        except DatabaseSchemaMissingError as exc:
            message = str(exc)
            schema_missing = True
            status = 503

        # Membership is checked against the whole selected-season catalog,
        # independently of the search text and the rendered result limit.
        selected_player = next(
            (player for player in catalog if player.player_id == player_id), None
        )
        if player_id is not None and selected_player is None and status == 200:
            message = f"Player {player_id} is not in the locally stored catalog" + (
                f" for {season}." if season is not None else "."
            )
            status = 404

        # A local read only, to decide whether a real overview link exists.
        # The directory itself never renders the stats.
        hitting_overview_url = None
        if selected_player is not None and season is not None:
            hitting = get_player_season_hitting(
                session, player_id=selected_player.player_id, season=season
            )
            if hitting is not None:
                hitting_overview_url = player_hitting_url(
                    season=season, player_id=selected_player.player_id
                )

        query = q.strip()
        matches = search_player_catalog(catalog, query)
        results = matches[:PLAYER_SEARCH_LIMIT]
        selection_links = {
            player.player_id: "/players?"
            + urlencode({"season": season, "q": query, "player_id": player.player_id})
            for player in results
        }
        return templates.TemplateResponse(
            request=request,
            name="players.html",
            context={
                "app_name": settings.app_name,
                "mlb_logo_url": MLB_LOGO_URL,
                "seasons": seasons,
                "season": season,
                "q": query,
                "has_search": bool(normalize_player_name(query)),
                "results": results,
                "selection_links": selection_links,
                "result_count": len(matches),
                "result_limit": PLAYER_SEARCH_LIMIT,
                "selected_player": selected_player,
                "hitting_overview_url": hitting_overview_url,
                "player_season_import_command": (
                    PLAYER_SEASON_IMPORT_COMMAND.format(
                        player_id=selected_player.player_id, season=season
                    )
                    if selected_player is not None
                    else None
                ),
                "message": message,
                "schema_missing": schema_missing,
                "migration_command": MIGRATION_HINT,
                "import_command": PLAYER_CATALOG_IMPORT_COMMAND.format(
                    season=season if season is not None else "<season>"
                ),
            },
            status_code=status,
        )

    @router.get(PLAYER_HITTING_PATH, response_class=HTMLResponse)
    def player_hitting(
        request: Request,
        session: Annotated[Session, Depends(get_db_session)],
        season: Annotated[int, Query(gt=0)],
        player_id: Annotated[int, Query(gt=0)],
    ) -> Response:
        """Render one Player's full-season hitting line from stored data only."""
        context: dict[str, object] = {
            "app_name": settings.app_name,
            "mlb_logo_url": MLB_LOGO_URL,
            "season": season,
            "player": None,
            "directory_url": PLAYER_DIRECTORY_PATH,
        }

        def render(state: str, status_code: int = 200) -> Response:
            context["state"] = state
            return templates.TemplateResponse(
                request=request,
                name="player_hitting.html",
                context=context,
                status_code=status_code,
            )

        try:
            player = get_player_catalog_entry(
                session, player_id=player_id, season=season
            )
        except DatabaseSchemaMissingError as exc:
            context["message"] = str(exc)
            context["migration_command"] = MIGRATION_HINT
            return render("schema_missing", 503)

        # Membership comes from the season's catalog, never from a stored
        # identity or a stored hitting row alone.
        if player is None:
            context["message"] = (
                f"Player {player_id} is not in the locally stored {season} "
                "Player catalog."
            )
            return render("not_found", 404)

        context["player"] = player
        context["directory_url"] = player_selection_url(
            season=season, player_id=player_id
        )
        context["import_command"] = PLAYER_SEASON_IMPORT_COMMAND.format(
            player_id=player_id, season=season
        )

        hitting = get_player_season_hitting(session, player_id=player_id, season=season)
        if hitting is None:
            # The selection is valid; only the analytics are unavailable, like
            # Team comparison without league data. Not zeroes, not an MLB call.
            return render("missing_hitting")

        try:
            overview = build_player_hitting_overview(hitting)
        except PlayerHittingAnalysisError as exc:
            context["message"] = str(exc)
            return render("inconsistent_hitting", 409)

        context["overview"] = overview
        context["rate_cards"] = build_player_hitting_rate_cards(overview)
        context["total_cards"] = build_player_hitting_total_cards(overview)
        if overview.plate_appearance_rates is not None:
            context["plotly_bundle_path"] = PLOTLY_BUNDLE_PATH
            context["chart_html"] = render_figure_html(
                build_player_plate_appearance_rates_figure(
                    overview.plate_appearance_rates
                ),
                div_id=PLAYER_PA_RATES_CHART_DIV_ID,
            )
        return render("ok")

    return router
