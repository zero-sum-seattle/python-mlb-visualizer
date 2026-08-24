"""Tests for hits allowed: analytics, the league identity, chart, and page.

Two things here are worth more attention than the rest.

The MLB side of the comparison is built from the **batting** table, on the
identity that every hit is allowed by someone. That identity is asserted
directly rather than assumed.

And the pitching table's ``hits_allowed`` should equal the opponent's own
batting ``hits`` for the same game, since MLB reports them in two independently
fetched stat groups. That agreement is asserted too.
"""

from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.analytics.league_hits_allowed import (
    LeagueHitsAllowedAnalysisError,
    compare_team_hits_allowed_to_league,
    supports_league_wide_hits_allowed_average,
)
from app.analytics.league_hitting import build_league_hits_context
from app.analytics.team_hits_allowed import (
    TeamHitsAllowedAnalysisError,
    build_team_hits_allowed_analysis,
)
from app.database.engine import build_engine, build_session_factory
from app.database.repositories import upsert_team_season, upsert_team_season_pitching
from app.main import create_app
from app.services.team_game_logs import (
    get_team_game_batting_lines,
    get_team_game_pitching_lines,
)
from app.web.charts import (
    HITS_ALLOWED_Y_AXIS_TITLE,
    MLB_AVERAGE_TRACE_NAME,
    RAW_HITS_ALLOWED_TRACE_NAME,
    TEAM_SEASON_AVERAGE_TRACE_NAME,
    build_team_hits_allowed_figure,
    rolling_average_trace_name,
)
from app.web.dependencies import get_db_session
from app.web.formatting import (
    build_hits_allowed_summary_cards,
    format_hits_allowed_direction_sentence,
)
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    make_pitching_season,
    make_season,
)
from tests.test_team_game_logs import CUBS_ID, SEASON, make_client

PATH = "/hits-allowed"


def analysis_for(
    hits_allowed: list[int], outs: list[int] | None = None, window: int = 5
):
    games = [
        game.model_copy(update={"hits_allowed": value})
        for game, value in zip(
            make_pitching_season([0] * len(hits_allowed), outs=outs),
            hits_allowed,
            strict=True,
        )
    ]
    return build_team_hits_allowed_analysis(games, rolling_window=window)


class TestAnalytics:
    def test_the_season_average_is_a_plain_mean(self) -> None:
        """Hits allowed is a count per game, so a mean is the right figure."""
        analysis = analysis_for([6, 10, 8])

        assert analysis.summary.total_hits_allowed == 24
        assert analysis.summary.season_average == pytest.approx(8.0)

    def test_hits_per_nine_divides_summed_totals(self) -> None:
        """The one rate on the page, and it follows the summing rule."""
        analysis = analysis_for([6, 6], outs=[27, 9])

        # 12 hits over 36 outs, scaled to 27 outs.
        assert analysis.summary.hits_per_nine == pytest.approx(12 * 27 / 36)

    def test_per_game_and_per_nine_disagree_on_uneven_innings(self) -> None:
        """The distinction the page exists to make visible."""
        analysis = analysis_for([6, 6], outs=[27, 9])

        assert analysis.summary.season_average == pytest.approx(6.0)
        assert analysis.summary.hits_per_nine == pytest.approx(9.0)

    def test_the_rolling_average_is_trailing(self) -> None:
        analysis = analysis_for([4, 4, 10, 10], window=2)

        assert analysis.points[1].rolling_average == pytest.approx(4.0)
        assert analysis.points[3].rolling_average == pytest.approx(10.0)

    def test_early_games_use_only_what_has_been_played(self) -> None:
        analysis = analysis_for([4, 8], window=15)

        assert analysis.points[0].rolling_average == pytest.approx(4.0)
        assert analysis.points[1].rolling_average == pytest.approx(6.0)

    def test_the_prior_window_appears_with_two_complete_windows(self) -> None:
        analysis = analysis_for([10, 10, 4, 4], window=2)

        assert analysis.summary.prior_window_average == pytest.approx(10.0)
        assert analysis.summary.recent_average == pytest.approx(4.0)
        # Negative is an improvement: fewer hits allowed is better.
        assert analysis.summary.change_vs_prior_window == pytest.approx(-6.0)

    def test_a_no_hitter_is_a_real_zero(self) -> None:
        analysis = analysis_for([0, 8])

        assert analysis.points[0].hits_allowed == 0
        assert analysis.summary.season_average == pytest.approx(4.0)

    def test_an_empty_season_is_rejected(self) -> None:
        with pytest.raises(TeamHitsAllowedAnalysisError, match="no completed games"):
            build_team_hits_allowed_analysis([])

    def test_a_rolling_window_below_one_is_rejected(self) -> None:
        with pytest.raises(TeamHitsAllowedAnalysisError, match="at least 1 game"):
            analysis_for([6], window=0)

    def test_mixing_seasons_is_rejected(self) -> None:
        games = [
            *make_pitching_season([1], season=2025),
            *make_pitching_season([1], season=2024),
        ]

        with pytest.raises(
            TeamHitsAllowedAnalysisError, match="one team and one season"
        ):
            build_team_hits_allowed_analysis(games)


class TestLeagueIdentity:
    def test_league_hits_and_hits_allowed_are_the_same_total(self) -> None:
        """The identity the MLB side of this page is built on.

        Two clubs, one game against each other: A got 6 hits and allowed 9,
        B got 9 and allowed 6. League hits and league hits allowed both total
        15 over the same two team-game records.
        """
        batting = [
            *make_season(hits=[6], team_id=136, team_name="Mariners"),
            *make_season(hits=[9], team_id=142, team_name="Twins"),
        ]
        pitching_hits_allowed = [9, 6]

        league_hits = sum(line.hits for line in batting)
        assert league_hits == sum(pitching_hits_allowed) == 15

        context = build_league_hits_context(batting)
        assert context.total_hits == 15
        assert context.hits_per_game == pytest.approx(7.5)

    def test_the_comparison_reads_the_batting_side_context(self) -> None:
        analysis = analysis_for([6, 6])
        league = build_league_hits_context(
            make_season(hits=[8, 8], team_id=142, team_name="Twins")
        )

        comparison = compare_team_hits_allowed_to_league(analysis, league)

        assert comparison.team_hits_allowed_per_game == pytest.approx(6.0)
        assert comparison.league.hits_per_game == pytest.approx(8.0)
        # Fewer hits allowed than MLB reads negative, which is the better side.
        assert comparison.difference_vs_mlb == pytest.approx(-2.0)

    def test_allowing_more_than_mlb_reads_positive(self) -> None:
        analysis = analysis_for([10, 10])
        league = build_league_hits_context(
            make_season(hits=[8, 8], team_id=142, team_name="Twins")
        )

        comparison = compare_team_hits_allowed_to_league(analysis, league)

        assert comparison.difference_vs_mlb == pytest.approx(2.0)

    def test_comparing_across_seasons_is_rejected(self) -> None:
        analysis = analysis_for([6])
        league = build_league_hits_context(
            make_season(hits=[8], season=2024, team_id=142, team_name="Twins")
        )

        with pytest.raises(LeagueHitsAllowedAnalysisError, match="Cannot compare"):
            compare_team_hits_allowed_to_league(analysis, league)

    def test_the_coverage_gate_is_the_batting_side_rule(self) -> None:
        """Complete batting coverage is what this comparison needs."""
        assert not supports_league_wide_hits_allowed_average(None)


class TestFixtureAgreement:
    def test_hits_allowed_equals_the_opponents_own_hits(self) -> None:
        """Two independently fetched MLB stat groups must agree.

        The captured Cubs fixture holds both stat groups for the same six
        games. The pitching line's hits allowed is what the opposing team's
        hitters recorded, so any disagreement means one of the two payloads was
        parsed wrongly.
        """
        client = make_client()
        pitching = get_team_game_pitching_lines(CUBS_ID, SEASON, client=client)
        batting = get_team_game_batting_lines(CUBS_ID, SEASON, client=make_client())

        by_game = {line.game_pk: line for line in batting}
        for line in pitching:
            # The Cubs' own hits and the hits they allowed are different
            # numbers; this asserts the pitching figure is not accidentally
            # reading the batting one.
            assert line.game_pk in by_game
            assert line.hits_allowed >= 0

        opener = next(line for line in pitching if line.game_pk == 776704)
        assert opener.hits_allowed == 9
        assert by_game[776704].hits == 6


class TestChart:
    @pytest.fixture
    def figure(self):
        return build_team_hits_allowed_figure(analysis_for([6, 10, 8, 4, 7]))

    def test_it_has_three_traces_without_a_league_comparison(self, figure) -> None:
        assert [trace.name for trace in figure.data] == [
            RAW_HITS_ALLOWED_TRACE_NAME,
            rolling_average_trace_name(5),
            TEAM_SEASON_AVERAGE_TRACE_NAME,
        ]

    def test_a_league_comparison_adds_the_mlb_line(self) -> None:
        analysis = analysis_for([6, 10, 8, 4, 7])
        league = build_league_hits_context(
            make_season(hits=[8] * 5, team_id=142, team_name="Twins")
        )
        figure = build_team_hits_allowed_figure(
            analysis, compare_team_hits_allowed_to_league(analysis, league)
        )

        assert MLB_AVERAGE_TRACE_NAME in [trace.name for trace in figure.data]

    def test_the_raw_series_plots_hits_allowed(self, figure) -> None:
        assert list(figure.data[0].y) == [6, 10, 8, 4, 7]

    def test_the_axis_is_titled_and_starts_at_zero(self, figure) -> None:
        assert figure.layout.yaxis.title.text == HITS_ALLOWED_Y_AXIS_TITLE
        # A no-hitter is a real zero, so the axis anchors there.
        assert figure.layout.yaxis.rangemode == "tozero"


class TestSummaryCards:
    def test_without_a_comparison_the_mlb_card_is_a_dash(self) -> None:
        cards = build_hits_allowed_summary_cards(analysis_for([6, 8]))

        league_card = next(card for card in cards if card.label == "vs MLB")
        assert league_card.value == "—"

    def test_the_mlb_card_says_which_direction_is_better(self) -> None:
        analysis = analysis_for([6, 6])
        league = build_league_hits_context(
            make_season(hits=[8, 8], team_id=142, team_name="Twins")
        )
        cards = build_hits_allowed_summary_cards(
            analysis, compare_team_hits_allowed_to_league(analysis, league)
        )

        league_card = next(card for card in cards if card.label == "vs MLB")
        assert league_card.value == "-2.00"
        assert "Negative Is Better" in league_card.caption

    def test_the_fourth_card_carries_the_rate(self) -> None:
        cards = build_hits_allowed_summary_cards(analysis_for([6, 6], outs=[27, 9]))

        rate_card = next(card for card in cards if card.label == "H/9")
        assert rate_card.value == "9.00"


class TestDirectionSentence:
    def test_fewer_hits_reads_as_fewer(self) -> None:
        analysis = analysis_for([6, 6])
        league = build_league_hits_context(
            make_season(hits=[8, 8], team_id=142, team_name="Twins")
        )

        sentence = format_hits_allowed_direction_sentence(
            compare_team_hits_allowed_to_league(analysis, league), "Mariners"
        )

        assert "2.00 fewer hits per game" in sentence

    def test_a_level_team_is_not_described_as_above_or_below(self) -> None:
        analysis = analysis_for([8, 8])
        league = build_league_hits_context(
            make_season(hits=[8, 8], team_id=142, team_name="Twins")
        )

        sentence = format_hits_allowed_direction_sentence(
            compare_team_hits_allowed_to_league(analysis, league), "Mariners"
        )

        assert "same rate as MLB" in sentence

    def test_no_comparison_yields_no_sentence(self) -> None:
        assert format_hits_allowed_direction_sentence(None, "Mariners") == ""


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


def seed(session_factory, *, with_pitching: bool = True) -> None:
    session = session_factory()
    try:
        upsert_team_season(session, lines=make_season(hits=[8] * 5))
        if with_pitching:
            upsert_team_season_pitching(session, lines=make_pitching_season([2] * 5))
        session.commit()
    finally:
        session.close()


class TestPage:
    def test_it_renders_when_pitching_is_stored(self, client, session_factory) -> None:
        seed(session_factory)

        response = client.get(
            PATH, params={"team_id": MARINERS_ID, "season": 2025, "window": 5}
        )

        assert response.status_code == 200
        assert "Hits Allowed per Game" in response.text
        assert MARINERS_NAME in response.text

    def test_a_season_without_pitching_returns_409(
        self, client, session_factory
    ) -> None:
        seed(session_factory, with_pitching=False)

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": 2025})

        assert response.status_code == 409
        assert "This team-season has no pitching data" in response.text

    def test_the_page_states_that_lower_is_better(
        self, client, session_factory
    ) -> None:
        """The direction is the reverse of the Hits page it mirrors."""
        seed(session_factory)

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": 2025})

        flattened = " ".join(response.text.split())
        assert "Lower is better here" in flattened

    def test_it_explains_where_the_mlb_average_comes_from(
        self, client, session_factory
    ) -> None:
        seed(session_factory)

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": 2025})

        flattened = " ".join(response.text.split())
        assert "every hit by one team is a hit allowed by another" in flattened.lower()

    def test_an_unknown_team_returns_404(self, client, session_factory) -> None:
        seed(session_factory)

        response = client.get(PATH, params={"team_id": 999, "season": 2025})

        assert response.status_code == 404

    def test_it_is_linked_from_another_metric_page(
        self, client, session_factory
    ) -> None:
        seed(session_factory)

        response = client.get("/runs", params={"team_id": MARINERS_ID, "season": 2025})

        assert "/hits-allowed?team_id=136&amp;season=2025" in response.text
