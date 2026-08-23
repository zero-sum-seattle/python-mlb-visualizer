"""Tests for pairing a team-season's games with the opponent's stored line.

``list_team_season_run_results`` is the only repository function that reads
two rows per game. Runs allowed is not a column: it is the opponent's own runs
scored for the same ``game_pk``, so these tests are mostly about the join
finding the right second row, and about what happens when there isn't one.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.database.repositories import (
    list_team_season_run_results,
    upsert_team_season,
)
from app.schemas.games import TeamGameBattingLine

CUBS_ID = 112
PIRATES_ID = 134
BREWERS_ID = 158
SEASON = 2025


def make_line(**overrides: object) -> TeamGameBattingLine:
    base = {
        "game_pk": 776704,
        "game_date": date(2025, 8, 17),
        "season": SEASON,
        "team_id": CUBS_ID,
        "team_name": "Chicago Cubs",
        "opponent_id": PIRATES_ID,
        "opponent_name": "Pittsburgh Pirates",
        "home_away": "home",
        "hits": 6,
        "runs": 4,
        "status": "Final",
        "game_number": 1,
        "doubleheader": False,
        "scheduled_innings": 9,
    }
    base.update(overrides)
    return TeamGameBattingLine(**base)


def store(session: Session, lines: list[TeamGameBattingLine]) -> None:
    """Persist lines that may span several clubs.

    ``upsert_team_season`` deliberately handles one team-season per call, which
    is how the real import scripts use it. Pairing tests need both clubs stored,
    so this groups the lines the way two separate imports would arrive.
    """
    grouped: dict[tuple[int, int], list[TeamGameBattingLine]] = {}
    for line in lines:
        grouped.setdefault((line.team_id, line.season), []).append(line)
    for group in grouped.values():
        upsert_team_season(session, lines=group)
    # The repository deliberately leaves committing to its caller, and the test
    # session does not autoflush, so the join below would see nothing without
    # this.
    session.commit()


def both_sides_of(
    *,
    game_pk: int,
    game_date: date,
    cubs_runs: int,
    pirates_runs: int,
    game_number: int = 1,
) -> tuple[TeamGameBattingLine, TeamGameBattingLine]:
    """Build the two rows one real game produces, one per club."""
    cubs = make_line(
        game_pk=game_pk,
        game_date=game_date,
        runs=cubs_runs,
        game_number=game_number,
        home_away="home",
    )
    pirates = make_line(
        game_pk=game_pk,
        game_date=game_date,
        team_id=PIRATES_ID,
        team_name="Pittsburgh Pirates",
        opponent_id=CUBS_ID,
        opponent_name="Chicago Cubs",
        runs=pirates_runs,
        game_number=game_number,
        home_away="away",
    )
    return cubs, pirates


def test_runs_allowed_comes_from_the_opponents_row(migrated_session: Session) -> None:
    cubs, pirates = both_sides_of(
        game_pk=776704, game_date=date(2025, 8, 17), cubs_runs=4, pirates_runs=9
    )
    store(migrated_session, [cubs, pirates])

    paired = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    )

    assert paired.unpaired_game_pks == ()
    (result,) = paired.results
    assert result.runs_scored == 4
    assert result.runs_allowed == 9
    assert result.run_differential == -5
    assert result.is_win is False


def test_the_same_game_is_symmetric_from_the_other_side(
    migrated_session: Session,
) -> None:
    """One game, two rows: each club's runs allowed is the other's runs scored."""
    cubs, pirates = both_sides_of(
        game_pk=776704, game_date=date(2025, 8, 17), cubs_runs=4, pirates_runs=9
    )
    store(migrated_session, [cubs, pirates])

    from_cubs = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    ).results[0]
    from_pirates = list_team_season_run_results(
        migrated_session, team_id=PIRATES_ID, season=SEASON
    ).results[0]

    assert from_cubs.runs_scored == from_pirates.runs_allowed == 4
    assert from_cubs.runs_allowed == from_pirates.runs_scored == 9
    assert from_cubs.run_differential == -from_pirates.run_differential
    assert from_cubs.is_win is not from_pirates.is_win


def test_a_single_team_import_reports_every_game_as_unpaired(
    migrated_session: Session,
) -> None:
    """No opponent rows exist, so nothing can be charted and nothing is invented."""
    cubs, _ = both_sides_of(
        game_pk=776704, game_date=date(2025, 8, 17), cubs_runs=4, pirates_runs=9
    )
    store(migrated_session, [cubs])

    paired = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    )

    assert paired.results == ()
    assert paired.unpaired_game_pks == (776704,)


def test_a_partially_imported_season_separates_paired_from_unpaired(
    migrated_session: Session,
) -> None:
    """The failure the outer join exists to expose: some opponents stored, some not."""
    paired_cubs, paired_pirates = both_sides_of(
        game_pk=776704, game_date=date(2025, 8, 17), cubs_runs=4, pirates_runs=9
    )
    lonely_cubs = make_line(
        game_pk=776705,
        game_date=date(2025, 8, 18),
        opponent_id=BREWERS_ID,
        opponent_name="Milwaukee Brewers",
        runs=7,
    )
    store(migrated_session, [paired_cubs, paired_pirates, lonely_cubs])

    paired = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    )

    assert [result.game_pk for result in paired.results] == [776704]
    assert paired.unpaired_game_pks == (776705,)


def test_results_come_back_in_chart_order(migrated_session: Session) -> None:
    lines: list[TeamGameBattingLine] = []
    # Inserted newest first so an unordered query would return them backwards.
    for game_pk, game_date, game_number in (
        (776706, date(2025, 8, 19), 1),
        (776705, date(2025, 8, 18), 2),
        (776704, date(2025, 8, 18), 1),
    ):
        lines.extend(
            both_sides_of(
                game_pk=game_pk,
                game_date=game_date,
                cubs_runs=5,
                pirates_runs=1,
                game_number=game_number,
            )
        )
    store(migrated_session, lines)

    paired = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    )

    assert [result.game_pk for result in paired.results] == [776704, 776705, 776706]


def test_a_doubleheader_pairs_each_game_separately(migrated_session: Session) -> None:
    """Two games share a date, so the join must key on game_pk, not the date."""
    lines: list[TeamGameBattingLine] = []
    lines.extend(
        both_sides_of(
            game_pk=776704,
            game_date=date(2025, 8, 18),
            cubs_runs=3,
            pirates_runs=1,
            game_number=1,
        )
    )
    lines.extend(
        both_sides_of(
            game_pk=776705,
            game_date=date(2025, 8, 18),
            cubs_runs=0,
            pirates_runs=6,
            game_number=2,
        )
    )
    store(migrated_session, lines)

    paired = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    )

    assert [
        (result.game_number, result.runs_scored, result.runs_allowed)
        for result in paired.results
    ] == [(1, 3, 1), (2, 0, 6)]


def test_another_seasons_games_are_not_paired_in(migrated_session: Session) -> None:
    this_year = both_sides_of(
        game_pk=776704, game_date=date(2025, 8, 17), cubs_runs=4, pirates_runs=9
    )
    last_year = tuple(
        line.model_copy(update={"season": 2024, "game_pk": 700001})
        for line in both_sides_of(
            game_pk=700001, game_date=date(2024, 8, 17), cubs_runs=1, pirates_runs=2
        )
    )
    store(migrated_session, [*this_year, *last_year])

    paired = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    )

    assert [result.game_pk for result in paired.results] == [776704]
    assert all(result.season == SEASON for result in paired.results)


def test_a_third_team_in_the_season_is_not_mistaken_for_the_opponent(
    migrated_session: Session,
) -> None:
    """The join matches on opponent_id, not merely on sharing a game_pk."""
    cubs, pirates = both_sides_of(
        game_pk=776704, game_date=date(2025, 8, 17), cubs_runs=4, pirates_runs=9
    )
    # A Brewers row that is not part of this game at all.
    brewers = make_line(
        game_pk=776799,
        game_date=date(2025, 8, 17),
        team_id=BREWERS_ID,
        team_name="Milwaukee Brewers",
        opponent_id=PIRATES_ID,
        opponent_name="Pittsburgh Pirates",
        runs=15,
    )
    store(migrated_session, [cubs, pirates, brewers])

    paired = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    )

    (result,) = paired.results
    assert result.runs_allowed == 9
    assert result.opponent_id == PIRATES_ID


def test_a_team_with_no_stored_games_returns_nothing(
    migrated_session: Session,
) -> None:
    paired = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    )

    assert paired.results == ()
    assert paired.unpaired_game_pks == ()


def test_the_selected_teams_identity_is_carried_not_the_opponents(
    migrated_session: Session,
) -> None:
    cubs, pirates = both_sides_of(
        game_pk=776704, game_date=date(2025, 8, 17), cubs_runs=4, pirates_runs=9
    )
    store(migrated_session, [cubs, pirates])

    (result,) = list_team_season_run_results(
        migrated_session, team_id=CUBS_ID, season=SEASON
    ).results

    assert result.team_id == CUBS_ID
    assert result.team_name == "Chicago Cubs"
    assert result.opponent_id == PIRATES_ID
    assert result.opponent_name == "Pittsburgh Pirates"
    assert result.home_away == "home"
