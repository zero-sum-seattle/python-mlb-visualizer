"""DB-only Player directory, search, and season-scoped selection."""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import Settings
from app.database.repositories import (
    MIGRATION_HINT,
    DatabaseSchemaMissingError,
    list_player_catalog,
    list_player_catalog_seasons,
)
from app.schemas.players import PlayerSeasonCatalogEntry, normalize_player_name
from app.web.dependencies import get_db_session
from app.web.routes import MLB_LOGO_URL

# A fixed cap keeps broad searches readable without a pagination framework.
PLAYER_SEARCH_LIMIT = 50
PLAYER_CATALOG_IMPORT_COMMAND = (
    "poetry run python scripts/import_player_catalog.py --season {season}"
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
    """Build the one real Player-domain destination."""
    router = APIRouter()

    @router.get("/players", response_class=HTMLResponse)
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
                "message": message,
                "schema_missing": schema_missing,
                "migration_command": MIGRATION_HINT,
                "import_command": PLAYER_CATALOG_IMPORT_COMMAND.format(
                    season=season if season is not None else "<season>"
                ),
            },
            status_code=status,
        )

    return router
