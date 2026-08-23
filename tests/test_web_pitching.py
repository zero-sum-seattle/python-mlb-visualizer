"""Tests for the /pitching page, its chart contract, and the pitching fixtures.

Covers the three things a reviewer would want proven end to end: that a
team-season without pitching rows is refused rather than charted, that the
figure plots pitch counts, and that the service turns MLB's baseball-notation
innings into exact outs.
"""

from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.analytics.team_pitching import build_team_pitching_analysis
from app.database.engine import build_engine, build_session_factory
from app.database.repositories import (
    list_team_season_pitching,
    upsert_team_season,
    upsert_team_season_pitching,
)
from app.main import create_app
from app.services.team_game_logs import get_team_game_pitching_lines
from app.web.charts import (
    PITCHING_CHART_DIV_ID,
    PITCHING_Y_AXIS_TITLE,
    RAW_PITCHES_TRACE_NAME,
    TEAM_SEASON_AVERAGE_TRACE_NAME,
    build_team_pitching_figure,
    render_figure_html,
    rolling_average_trace_name,
)
from app.web.dependencies import get_db_session
from app.web.formatting import build_pitching_summary_cards, format_innings
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    make_pitching_season,
    make_season,
)
from tests.test_team_game_logs import CUBS_ID, SEASON, make_client

PATH = "/pitching"


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


def seed(
    session_factory: Callable[[], Session],
    *,
    with_pitching: bool = True,
    earned_runs: list[int] | None = None,
) -> None:
    """Store a team-season, optionally including its pitching lines."""
    runs = earned_runs or [2, 4, 1, 5, 3]
    session = session_factory()
    try:
        upsert_team_season(session, lines=make_season(hits=[8] * len(runs)))
        if with_pitching:
            upsert_team_season_pitching(session, lines=make_pitching_season(runs))
        session.commit()
    finally:
        session.close()


class TestPageStates:
    def test_the_page_renders_when_pitching_is_stored(
        self, client: TestClient, session_factory
    ) -> None:
        seed(session_factory)

        response = client.get(
            PATH, params={"team_id": MARINERS_ID, "season": 2025, "window": 5}
        )

        assert response.status_code == 200
        assert "Pitches per Game" in response.text
        assert MARINERS_NAME in response.text

    def test_a_season_without_pitching_returns_409(
        self, client: TestClient, session_factory
    ) -> None:
        """Batting rows exist; pitching was never fetched for them."""
        seed(session_factory, with_pitching=False)

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": 2025})

        assert response.status_code == 409
        assert "This team-season has no pitching data" in response.text

    def test_the_409_names_the_team_reimport_as_the_fix(
        self, client: TestClient, session_factory
    ) -> None:
        """Pitching is a separate request, so only a re-import can supply it."""
        seed(session_factory, with_pitching=False)

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": 2025})

        assert "import_team_season.py --team-id 136 --season 2025" in response.text

    def test_no_imported_data_shows_the_import_command(
        self, client: TestClient
    ) -> None:
        response = client.get(PATH)

        assert response.status_code == 200
        assert "No team data has been imported yet" in response.text

    def test_an_unknown_team_returns_404(
        self, client: TestClient, session_factory
    ) -> None:
        seed(session_factory)

        response = client.get(PATH, params={"team_id": 999, "season": 2025})

        assert response.status_code == 404

    def test_an_unsupported_window_is_rejected(
        self, client: TestClient, session_factory
    ) -> None:
        seed(session_factory)

        response = client.get(
            PATH, params={"team_id": MARINERS_ID, "season": 2025, "window": 7}
        )

        assert response.status_code == 422

    def test_the_page_is_linked_from_another_metric_page(
        self, client: TestClient, session_factory
    ) -> None:
        seed(session_factory)

        response = client.get("/runs", params={"team_id": MARINERS_ID, "season": 2025})

        assert "/pitching?team_id=136&amp;season=2025" in response.text


class TestChartContract:
    @pytest.fixture
    def figure(self):
        analysis = build_team_pitching_analysis(
            make_pitching_season([2, 4, 1, 5, 3]), rolling_window=5
        )
        return build_team_pitching_figure(analysis)

    def test_it_has_pitches_rolling_and_season_average(self, figure) -> None:
        assert [trace.name for trace in figure.data] == [
            RAW_PITCHES_TRACE_NAME,
            rolling_average_trace_name(5),
            TEAM_SEASON_AVERAGE_TRACE_NAME,
        ]

    def test_the_raw_series_plots_pitch_counts(self, figure) -> None:
        raw = figure.data[0]
        # 150 pitches per game from the factory.
        assert list(raw.y) == [150, 150, 150, 150, 150]

    def test_the_y_axis_is_titled_for_pitches(self, figure) -> None:
        assert figure.layout.yaxis.title.text == PITCHING_Y_AXIS_TITLE

    def test_the_axis_does_not_anchor_at_zero(self, figure) -> None:
        """A team throws ~100 pitches minimum; zero would waste the plot."""
        assert figure.layout.yaxis.rangemode != "tozero"

    def test_there_is_no_mlb_reference_trace(self, figure) -> None:
        assert "MLB Average" not in [trace.name for trace in figure.data]

    def test_it_renders_into_the_expected_div(self, figure) -> None:
        assert PITCHING_CHART_DIV_ID in render_figure_html(
            figure, div_id=PITCHING_CHART_DIV_ID
        )


class TestSummaryCards:
    def test_there_are_four_cards_and_none_compares_to_mlb(self) -> None:
        analysis = build_team_pitching_analysis(
            make_pitching_season([2, 4]), rolling_window=2
        )

        cards = build_pitching_summary_cards(analysis)

        assert len(cards) == 4
        assert "vs MLB" not in [card.label for card in cards]

    def test_the_season_card_reports_innings_in_baseball_notation(self) -> None:
        analysis = build_team_pitching_analysis(
            make_pitching_season([9], outs=[32]), rolling_window=1
        )

        cards = build_pitching_summary_cards(analysis)
        era_card = next(card for card in cards if card.label == "Season ERA")

        assert "10.2 IP" in era_card.caption
        assert era_card.value == "7.59"


class TestFormatInnings:
    @pytest.mark.parametrize(
        ("outs", "expected"),
        [(27, "9.0"), (28, "9.1"), (29, "9.2"), (32, "10.2"), (4388, "1,462.2")],
    )
    def test_thirds_are_rendered_as_the_fractional_digit(
        self, outs: int, expected: str
    ) -> None:
        """The digit after the point counts thirds, and is never a decimal."""
        assert format_innings(outs) == expected


class TestServiceParsesTheFixture:
    def test_pitching_lines_are_normalized_from_the_captured_payload(self) -> None:
        lines = get_team_game_pitching_lines(CUBS_ID, SEASON, client=make_client())

        assert len(lines) == 6
        opener = next(line for line in lines if line.game_pk == 776704)
        assert opener.outs == 27
        assert opener.innings_pitched_display == "9.0"
        assert (opener.hits_allowed, opener.runs_allowed, opener.earned_runs) == (
            9,
            3,
            3,
        )
        assert (opener.base_on_balls, opener.strikeouts) == (3, 9)
        assert opener.batters_faced == 40

    def test_unearned_runs_are_kept_distinct_from_earned_ones(self) -> None:
        """Game 777459 in the fixture is 7 runs allowed but only 6 earned."""
        lines = get_team_game_pitching_lines(CUBS_ID, SEASON, client=make_client())

        game = next(line for line in lines if line.game_pk == 777459)
        assert game.runs_allowed == 7
        assert game.earned_runs == 6

    def test_balls_are_derived_rather_than_stored(self) -> None:
        lines = get_team_game_pitching_lines(CUBS_ID, SEASON, client=make_client())

        game = lines[0]
        assert game.balls == game.number_of_pitches - game.strikes


class TestRepository:
    def test_pitching_lines_round_trip(self, session_factory) -> None:
        seed(session_factory, earned_runs=[3, 1])

        session = session_factory()
        try:
            stored = list_team_season_pitching(
                session, team_id=MARINERS_ID, season=2025
            )
        finally:
            session.close()

        assert [line.earned_runs for line in stored] == [3, 1]
        assert all(line.outs == 27 for line in stored)

    def test_a_season_without_pitching_returns_nothing(self, session_factory) -> None:
        seed(session_factory, with_pitching=False)

        session = session_factory()
        try:
            stored = list_team_season_pitching(
                session, team_id=MARINERS_ID, season=2025
            )
        finally:
            session.close()

        assert stored == []

    def test_reimporting_the_same_lines_is_idempotent(self, session_factory) -> None:
        seed(session_factory, earned_runs=[3, 1])
        session = session_factory()
        try:
            result = upsert_team_season_pitching(
                session, lines=make_pitching_season([3, 1])
            )
            session.commit()
        finally:
            session.close()

        assert (result.inserted, result.updated, result.unchanged) == (0, 0, 2)
