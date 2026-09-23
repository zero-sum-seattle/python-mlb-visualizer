"""Characterization tests for the team/season page lifecycle every analytics
route shares before it loads any metric-specific records.

Each analytics page resolves a team, a season, and a rolling window from the
query string, handles the empty-database and not-found states, and carries the
reader's selection through the form and the navigation. That behavior is the
same on every page, so it is pinned here once per route. What each page does
after the selection is resolved (loading its own records, missing-data states,
league context, charts, cards) is covered by that route's own test module.

The key contract: terminal states (empty, unknown team, unknown season) keep
the *requested* query values in the navigation, while a resolved selection
rebuilds the navigation from the *resolved* values.
"""

import html
import re
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.engine import build_engine, build_session_factory
from app.database.repositories import upsert_team_season, upsert_team_season_pitching
from app.main import create_app
from app.schemas.games import TeamGameBattingLine
from app.web.dependencies import get_db_session
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    TWINS_ID,
    TWINS_NAME,
    make_batting_line,
    make_pitching_season,
    make_season,
)

BROWSER_HEADERS = {"accept": "text/html,application/xhtml+xml"}
GAMES = 20


@dataclass(frozen=True)
class AnalyticsPage:
    path: str
    heading: str
    nav_label: str


PAGES = (
    AnalyticsPage("/", "Team Hitting Trends", "Hits"),
    AnalyticsPage("/strikeouts", "Team Batting Strikeout Trends", "Batting Strikeouts"),
    AnalyticsPage("/runs", "Team Run Scoring Trends", "Runs"),
    AnalyticsPage("/baserunners", "Team Baserunners Trends", "Baserunners"),
    AnalyticsPage("/run-differential", "Team Run Differential", "Run Differential"),
    AnalyticsPage("/pitching", "Team Pitching Trends", "Pitching"),
    AnalyticsPage("/hits-allowed", "Team Hits Allowed Trends", "Hits Allowed"),
    AnalyticsPage("/comparison", "Team Hitting Trends Comparison", "Comparison"),
)
PAGE_PATHS = tuple(page.path for page in PAGES)

_NAV_LINK_PATTERN = re.compile(
    r'<a class="site-nav__link(?P<current> site-nav__link--current)?"\s+'
    r'href="(?P<href>[^"]*)"\s*(?:aria-current="page")?>(?P<label>[^<]+)</a>'
)


@dataclass(frozen=True)
class RenderedNavLink:
    label: str
    href: str
    is_current: bool


def rendered_nav_links(body: str) -> list[RenderedNavLink]:
    links = [
        RenderedNavLink(
            label=match.group("label"),
            href=html.unescape(match.group("href")),
            is_current=match.group("current") is not None,
        )
        for match in _NAV_LINK_PATTERN.finditer(body)
    ]
    assert len(links) == len(PAGES), "the page did not render the full navigation"
    return links


def assert_navigation(body: str, *, current: AnalyticsPage, query: str) -> None:
    """Every link carries ``query``, and only ``current`` is marked current."""
    suffix = f"?{query}" if query else ""
    links = rendered_nav_links(body)
    assert [link.href for link in links] == [f"{page.path}{suffix}" for page in PAGES]
    assert [link.label for link in links if link.is_current] == [current.nav_label]
    assert body.count('aria-current="page"') == 1


def expected_query(team_id: int | None, season: int | None, window: int) -> str:
    selection: dict[str, int] = {}
    if team_id is not None:
        selection["team_id"] = team_id
    if season is not None:
        selection["season"] = season
    selection["window"] = window
    return urlencode(selection)


def page_for(path: str) -> AnalyticsPage:
    return next(page for page in PAGES if page.path == path)


@pytest.fixture
def session_factory(migrated_db_path: Path) -> Generator[Callable[[], Session]]:
    engine = build_engine(f"sqlite:///{migrated_db_path}")
    factory = build_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def client(session_factory: Callable[[], Session]) -> TestClient:
    app = create_app()

    def override_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


def opponent_rows(lines: list[TeamGameBattingLine]) -> list[TeamGameBattingLine]:
    """The Twins' side of each Seattle game, as a league-wide import stores it."""
    return [
        make_batting_line(
            game_pk=line.game_pk,
            game_date=line.game_date,
            season=line.season,
            team_id=TWINS_ID,
            team_name=TWINS_NAME,
            opponent_id=MARINERS_ID,
            opponent_name=MARINERS_NAME,
            home_away="away" if line.home_away == "home" else "home",
            runs=3,
            strikeouts=9,
            base_on_balls=2,
            hit_by_pitch=0,
        )
        for line in lines
    ]


@pytest.fixture
def seeded(session_factory: Callable[[], Session]) -> None:
    """Store enough for every page to reach its selected-team data.

    Seattle holds 2024 and 2025. Each season has batting lines with every
    optional column known, pitching lines, and the Twins' rows for the same
    games so run differential can pair them. The Twins are a second stored
    team, so the default-team rule has a real choice to make.
    """
    mariners_2025 = make_season(
        hits=[8] * GAMES,
        strikeouts=[7] * GAMES,
        base_on_balls=[3] * GAMES,
        hit_by_pitch=[1] * GAMES,
        runs=[4] * GAMES,
    )
    mariners_2024 = make_season(
        hits=[6] * GAMES,
        season=2024,
        strikeouts=[8] * GAMES,
        base_on_balls=[2] * GAMES,
        hit_by_pitch=[0] * GAMES,
    )
    session = session_factory()
    try:
        for mariners in (mariners_2025, mariners_2024):
            upsert_team_season(session, lines=mariners)
            upsert_team_season(session, lines=opponent_rows(mariners))
        upsert_team_season_pitching(
            session, lines=make_pitching_season([3] * GAMES, season=2025)
        )
        upsert_team_season_pitching(
            session, lines=make_pitching_season([3] * GAMES, season=2024)
        )
        session.commit()
    finally:
        session.close()


@pytest.mark.parametrize("path", PAGE_PATHS)
class TestEmptyDatabase:
    def test_renders_the_route_empty_state(self, client: TestClient, path: str) -> None:
        response = client.get(path)
        assert response.status_code == 200
        assert f"<h1>{page_for(path).heading}</h1>" in response.text
        assert "No team data has been imported yet" in response.text
        assert "--team-id 136 --season 2025" in response.text
        # No stored team means no selector form to fill in.
        assert '<select id="team_id"' not in response.text

    def test_navigation_carries_the_default_window(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(path).text
        assert_navigation(body, current=page_for(path), query="window=15")

    def test_navigation_carries_the_requested_values(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(f"{path}?team_id=147&season=2023&window=5").text
        assert_navigation(
            body,
            current=page_for(path),
            query=expected_query(147, 2023, 5),
        )


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
class TestUnknownTeam:
    def test_is_a_404_with_the_existing_message(
        self, client: TestClient, path: str
    ) -> None:
        response = client.get(f"{path}?team_id=999&season=2025&window=10")
        assert response.status_code == 404
        assert f"<h1>{page_for(path).heading}</h1>" in response.text
        assert "That team-season is not stored locally" in response.text
        assert (
            "No games are stored for team id 999. "
            "Pick a team that has been imported, or import that team."
        ) in response.text

    def test_keeps_the_team_selector_populated_with_nothing_selected(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(f"{path}?team_id=999&season=2025&window=10").text
        assert f'<option value="{MARINERS_ID}">{MARINERS_NAME}</option>' in body
        assert f'<option value="{TWINS_ID}">{TWINS_NAME}</option>' in body
        assert '<option value="10" selected>10 Games</option>' in body
        assert f'action="{path}"' in body

    def test_navigation_carries_the_requested_values(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(f"{path}?team_id=999&season=2025&window=10").text
        assert_navigation(
            body, current=page_for(path), query=expected_query(999, 2025, 10)
        )

    def test_navigation_omits_a_season_that_was_not_requested(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(f"{path}?team_id=999").text
        assert_navigation(
            body, current=page_for(path), query=expected_query(999, None, 15)
        )


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
class TestUnknownSeason:
    def test_is_a_404_listing_the_stored_seasons(
        self, client: TestClient, path: str
    ) -> None:
        response = client.get(f"{path}?team_id=136&season=1998&window=30")
        assert response.status_code == 404
        assert f"<h1>{page_for(path).heading}</h1>" in response.text
        assert (
            "No 1998 games are stored for Seattle Mariners. Stored seasons: 2025, 2024."
        ) in response.text

    def test_keeps_the_team_selected_and_its_seasons_offered(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(f"{path}?team_id=136&season=1998&window=30").text
        assert f'<option value="{MARINERS_ID}" selected>{MARINERS_NAME}</option>' in (
            body
        )
        assert '<option value="2025">2025</option>' in body
        assert '<option value="2024">2024</option>' in body
        assert '<option value="30" selected>30 Games</option>' in body

    def test_navigation_carries_the_requested_values(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(f"{path}?team_id=136&season=1998&window=30").text
        assert_navigation(
            body, current=page_for(path), query=expected_query(136, 1998, 30)
        )


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
class TestExplicitSelection:
    def test_form_reflects_the_selection_and_posts_to_this_page(
        self, client: TestClient, path: str
    ) -> None:
        response = client.get(f"{path}?team_id=136&season=2024&window=5")
        assert response.status_code == 200
        body = response.text
        assert f'<form class="controls card" method="get" action="{path}">' in body
        assert f'<option value="{MARINERS_ID}" selected>{MARINERS_NAME}</option>' in (
            body
        )
        assert '<option value="2024" selected>2024</option>' in body
        assert '<option value="5" selected>5 Games</option>' in body

    def test_navigation_preserves_the_complete_query(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(f"{path}?team_id=136&season=2024&window=5").text
        assert_navigation(
            body, current=page_for(path), query=expected_query(136, 2024, 5)
        )


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
class TestDefaultSelection:
    def test_resolves_seattle_and_its_newest_season(
        self, client: TestClient, path: str
    ) -> None:
        response = client.get(path)
        assert response.status_code == 200
        assert f'<option value="{MARINERS_ID}" selected>{MARINERS_NAME}</option>' in (
            response.text
        )
        assert '<option value="2025" selected>2025</option>' in response.text
        assert '<option value="15" selected>15 Games</option>' in response.text

    def test_navigation_is_rebuilt_from_the_resolved_values(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(path).text
        assert_navigation(
            body, current=page_for(path), query=expected_query(136, 2025, 15)
        )

    def test_a_requested_team_resolves_its_newest_season(
        self, client: TestClient, path: str
    ) -> None:
        body = client.get(f"{path}?team_id=136&window=30").text
        assert_navigation(
            body, current=page_for(path), query=expected_query(136, 2025, 30)
        )


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.parametrize(
    ("query", "parameter"),
    [
        ("team_id=0", "team_id"),
        ("team_id=-3", "team_id"),
        ("season=0", "season"),
        ("season=-2025", "season"),
        ("window=7", "window"),
        ("window=banana", "window"),
        ("team_id=banana", "team_id"),
        ("season=20.5", "season"),
    ],
)
class TestInvalidQueryValues:
    def test_is_rejected_by_fastapi_validation(
        self, client: TestClient, path: str, query: str, parameter: str
    ) -> None:
        response = client.get(f"{path}?{query}", headers={"accept": "application/json"})
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["query", parameter]

    def test_browsers_get_the_readable_error_page(
        self, client: TestClient, path: str, query: str, parameter: str
    ) -> None:
        response = client.get(f"{path}?{query}", headers=BROWSER_HEADERS)
        assert response.status_code == 422
        assert "That link has a value this page cannot use" in response.text
        assert f"{parameter}: " in response.text
        assert "Traceback" not in response.text


def test_window_error_lists_the_allowed_values(client: TestClient) -> None:
    for path in PAGE_PATHS:
        response = client.get(f"{path}?window=7", headers=BROWSER_HEADERS)
        assert "Input should be 5, 10, 15 or 30" in response.text


@pytest.fixture
def unmigrated_client(tmp_path: Path) -> Generator[TestClient]:
    engine = build_engine(f"sqlite:///{tmp_path / 'unmigrated.db'}")
    factory = build_session_factory(engine)
    app = create_app()

    def override_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    try:
        yield TestClient(app)
    finally:
        engine.dispose()


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_missing_schema_renders_the_migration_error_page(
    unmigrated_client: TestClient, path: str
) -> None:
    response = unmigrated_client.get(f"{path}?team_id=136&season=2025&window=15")
    assert response.status_code == 503
    body = response.text
    assert "The database schema is not ready" in body
    assert "poetry run alembic upgrade head" in body
    assert "Traceback" not in body
    # error.html, not the route template: no page heading, selector, or nav.
    assert f"<h1>{page_for(path).heading}</h1>" not in body
    assert '<select id="team_id"' not in body
    assert 'aria-label="Metrics"' not in body


# What each page shows once the seeded selection reaches its own data. The
# comparison page has no COMPLETE league coverage in this fixture, so its
# route-specific "unavailable" state is the expected outcome there.
SEEDED_OUTCOMES = {
    "/": "Seattle Mariners — Hits per Game",
    "/strikeouts": "Seattle Mariners — Batting Strikeouts per Game",
    "/runs": "Seattle Mariners — Runs Scored per Game",
    "/baserunners": "Seattle Mariners — Baserunners per Game",
    "/run-differential": "Seattle Mariners — Run Differential per Game",
    "/pitching": "Seattle Mariners &mdash; Pitches per Game",
    "/hits-allowed": "Seattle Mariners &mdash; Hits Allowed per Game",
    "/comparison": "Normalized comparison unavailable",
}


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
def test_seeded_selection_reaches_the_route_specific_page(
    client: TestClient, path: str
) -> None:
    """Guard the fixture: the selection tests above exercise a resolved page."""
    response = client.get(path)
    assert response.status_code == 200
    assert SEEDED_OUTCOMES[path] in response.text
