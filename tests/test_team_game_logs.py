"""Tests for the team game log retrieval and normalization service.

Fixtures live in ``tests/fixtures/team_game_logs``. The ``cubs_2025_*`` files are
trimmed captures of real MLB Stats API payloads for the 2025 Chicago Cubs,
covering a home series, an away series, a doubleheader, and the duplicate
schedule entry that a postponed and made up game produces. The ``edge_cases_*``
files are synthetic rows built from those same structures to cover in progress,
suspended, cancelled, and postponed states that the completed 2025 regular
season does not contain.

Nothing here touches the network: the ``mlbstatsapi.Mlb`` client is replaced at
the service boundary and payloads are parsed with the library's own models.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from mlbstatsapi.exceptions import (
    MlbDecodeError,
    MlbHttpError,
    MlbTimeoutError,
    MlbTransportError,
    TheMlbStatsApiException,
)
from mlbstatsapi.mlb_module import create_split_data
from mlbstatsapi.models.schedules import Schedule
from mlbstatsapi.models.stats import Stat
from mlbstatsapi.models.teams import Team

from app.schemas.games import TeamGameBattingLine
from app.services import team_game_logs
from app.services.team_game_logs import (
    TeamGameDataError,
    TeamGameLogError,
    TeamNotFoundError,
    get_team_game_batting_lines,
)

FIXTURES = Path(__file__).parent / "fixtures" / "team_game_logs"
CUBS_ID = 112
SEASON = 2025

MLB_SPORT = {"id": 1, "link": "/api/v1/sports/1", "name": "Major League Baseball"}
CUBS = Team(id=CUBS_ID, link="/api/v1/teams/112", name="Chicago Cubs", sport=MLB_SPORT)


def load_payload(name: str) -> dict[str, Any]:
    """Load a raw MLB Stats API payload fixture."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def build_team_stats(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the dict that ``Mlb.get_team_stats`` returns for a payload."""
    return create_split_data(payload["stats"])


def build_schedule(payload: dict[str, Any]) -> Schedule:
    """Build the Schedule that ``Mlb.get_schedule`` returns for a payload."""
    return Schedule(**payload)


class FakeMlb:
    """Stands in for ``mlbstatsapi.Mlb`` at the service boundary.

    Any of the three return values may be an exception instance, which is raised
    instead of returned.
    """

    def __init__(
        self,
        *,
        team: Team | Exception | None = CUBS,
        team_stats: dict[str, Any] | Exception | None = None,
        schedule: Schedule | Exception | None = None,
    ) -> None:
        self._team = team
        self._team_stats = team_stats if team_stats is not None else {}
        self._schedule = schedule
        self.calls: dict[str, dict[str, Any]] = {}
        self.closed = False

    @staticmethod
    def _resolve(value: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return value

    def get_team(self, team_id: int, **params: Any) -> Team | None:
        self.calls["get_team"] = {"team_id": team_id, **params}
        return self._resolve(self._team)

    def get_team_stats(
        self,
        team_id: int,
        stats: list[str],
        groups: list[str],
        **params: Any,
    ) -> dict[str, Any]:
        self.calls["get_team_stats"] = {
            "team_id": team_id,
            "stats": stats,
            "groups": groups,
            **params,
        }
        return self._resolve(self._team_stats)

    def get_schedule(self, **params: Any) -> Schedule | None:
        self.calls["get_schedule"] = params
        return self._resolve(self._schedule)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeMlb":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def make_client(
    game_log: str = "cubs_2025_hitting_game_log",
    schedule: str = "cubs_2025_schedule",
) -> FakeMlb:
    """Build a fake client backed by the named fixtures."""
    return FakeMlb(
        team_stats=build_team_stats(load_payload(game_log)),
        schedule=build_schedule(load_payload(schedule)),
    )


def remove_schedule_score(payload: dict[str, Any], game_pk: int, side: str) -> None:
    """Drop one side's score from a schedule entry; the upstream field is optional."""
    for schedule_date in payload["dates"]:
        for game in schedule_date["games"]:
            if game["gamePk"] == game_pk:
                game["teams"][side].pop("score", None)


def collect(client: FakeMlb) -> list[TeamGameBattingLine]:
    return get_team_game_batting_lines(CUBS_ID, SEASON, client=client)


def by_game_pk(lines: list[TeamGameBattingLine]) -> dict[int, TeamGameBattingLine]:
    return {line.game_pk: line for line in lines}


def test_completed_home_game_is_normalized() -> None:
    line = by_game_pk(collect(make_client()))[776704]
    assert line == TeamGameBattingLine(
        game_pk=776704,
        game_date="2025-08-17",
        season=2025,
        team_id=CUBS_ID,
        team_name="Chicago Cubs",
        opponent_id=134,
        opponent_name="Pittsburgh Pirates",
        home_away="home",
        hits=6,
        runs=4,
        strikeouts=5,
        base_on_balls=5,
        hit_by_pitch=0,
        status="Final",
        game_number=1,
        doubleheader=False,
        scheduled_innings=9,
    )


def test_completed_away_game_is_normalized() -> None:
    line = by_game_pk(collect(make_client()))[776640]
    assert line == TeamGameBattingLine(
        game_pk=776640,
        game_date="2025-08-22",
        season=2025,
        team_id=CUBS_ID,
        team_name="Chicago Cubs",
        opponent_id=108,
        opponent_name="Los Angeles Angels",
        home_away="away",
        hits=5,
        runs=3,
        strikeouts=10,
        base_on_balls=4,
        hit_by_pitch=0,
        status="Final",
        game_number=1,
        doubleheader=False,
        scheduled_innings=9,
    )


def test_hits_and_runs_belong_to_the_selected_team() -> None:
    """The 2025-08-23 game at the Angels ended 12-1, so a swap would be obvious."""
    line = by_game_pk(collect(make_client()))[776618]
    assert (line.home_away, line.opponent_id) == ("away", 108)
    assert (line.hits, line.runs) == (14, 12)


def test_postponed_games_are_excluded() -> None:
    lines = collect(make_client("edge_cases_hitting_game_log", "edge_cases_schedule"))
    assert 776504 not in by_game_pk(lines)


def test_cancelled_games_are_excluded() -> None:
    lines = collect(make_client("edge_cases_hitting_game_log", "edge_cases_schedule"))
    assert 776503 not in by_game_pk(lines)


def test_in_progress_games_are_excluded() -> None:
    lines = collect(make_client("edge_cases_hitting_game_log", "edge_cases_schedule"))
    assert 776501 not in by_game_pk(lines)


def test_suspended_games_are_excluded() -> None:
    lines = collect(make_client("edge_cases_hitting_game_log", "edge_cases_schedule"))
    assert 776502 not in by_game_pk(lines)


def test_only_completed_edge_case_game_is_returned() -> None:
    lines = collect(make_client("edge_cases_hitting_game_log", "edge_cases_schedule"))
    assert [line.game_pk for line in lines] == [776500]


def test_both_doubleheader_games_are_retained() -> None:
    doubleheader = [line for line in collect(make_client()) if line.doubleheader]
    on_date = [
        line for line in doubleheader if line.game_date.isoformat() == "2025-08-19"
    ]
    assert [(line.game_pk, line.game_number, line.hits) for line in on_date] == [
        (776691, 1, 8),
        (776676, 2, 9),
    ]


def test_made_up_game_uses_its_completed_schedule_entry() -> None:
    """Game 776691 is listed twice: postponed on 08-18 and played on 08-19."""
    line = by_game_pk(collect(make_client()))[776691]
    assert (line.status, line.game_number, line.game_date.isoformat()) == (
        "Final",
        1,
        "2025-08-19",
    )


def test_records_are_sorted_deterministically() -> None:
    """Game 776676 is the second game of a doubleheader despite the lower id."""
    payload = load_payload("cubs_2025_hitting_game_log")
    payload["stats"][0]["splits"].reverse()
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    assert [line.game_pk for line in collect(client)] == [
        776704,
        777459,
        776691,
        776676,
        776640,
        776618,
    ]


def test_identical_duplicate_splits_do_not_duplicate_records() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    splits = payload["stats"][0]["splits"]
    splits.append(copy.deepcopy(splits[0]))
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    lines = collect(client)
    assert len(lines) == 6
    assert len({line.game_pk for line in lines}) == 6
    assert by_game_pk(lines)[776704].hits == 6


def test_conflicting_duplicate_hits_raise_data_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    splits = payload["stats"][0]["splits"]
    conflicting = copy.deepcopy(splits[0])
    conflicting["stat"]["hits"] = 9
    splits.append(conflicting)
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    message = str(excinfo.value)
    assert "Conflicting duplicate game log records" in message
    assert "776704" in message
    assert "hits 6 vs 9" in message


def test_conflicting_duplicate_runs_raise_data_error() -> None:
    """The schedule score is dropped so the score invariant does not fire first."""
    payload = load_payload("cubs_2025_hitting_game_log")
    splits = payload["stats"][0]["splits"]
    conflicting = copy.deepcopy(splits[0])
    conflicting["stat"]["runs"] = 9
    splits.append(conflicting)
    schedule = load_payload("cubs_2025_schedule")
    remove_schedule_score(schedule, 776704, "home")
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(schedule),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    message = str(excinfo.value)
    assert "Conflicting duplicate game log records" in message
    assert "776704" in message
    assert "runs 4 vs 9" in message


def test_split_for_another_team_raises_data_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    payload["stats"][0]["splits"][0]["team"]["id"] = 999
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    message = str(excinfo.value)
    assert "reports team 999" in message
    assert "776704" in message


def test_split_without_a_team_raises_data_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    del payload["stats"][0]["splits"][0]["team"]
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError, match="776704"):
        collect(client)


def test_opponent_mismatch_raises_data_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    payload["stats"][0]["splits"][0]["opponent"]["id"] = 999
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    message = str(excinfo.value)
    assert "opponent 999 does not match scheduled opponent 134" in message
    assert "776704" in message


def test_home_away_mismatch_raises_data_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    payload["stats"][0]["splits"][0]["isHome"] = False
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    message = str(excinfo.value)
    assert "is_home=False" in message
    assert "home" in message
    assert "776704" in message


def test_split_date_disagreeing_with_official_date_raises_data_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    payload["stats"][0]["splits"][0]["date"] = "2025-08-16"
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    message = str(excinfo.value)
    assert "2025-08-16 does not match official schedule date 2025-08-17" in message
    assert "776704" in message


def test_unparsable_official_schedule_date_raises_data_error() -> None:
    payload = load_payload("cubs_2025_schedule")
    for schedule_date in payload["dates"]:
        for game in schedule_date["games"]:
            if game["gamePk"] == 776704:
                game["officialDate"] = "08/17/2025"
    client = FakeMlb(
        team_stats=build_team_stats(load_payload("cubs_2025_hitting_game_log")),
        schedule=build_schedule(payload),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    message = str(excinfo.value)
    assert "official schedule date '08/17/2025'" in message
    assert "776704" in message


def test_runs_disagreeing_with_schedule_score_raise_data_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    payload["stats"][0]["splits"][0]["stat"]["runs"] = 9
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    message = str(excinfo.value)
    assert "runs 9 do not match the scheduled score 4" in message
    assert "776704" in message


def test_missing_schedule_score_does_not_fail_normalization() -> None:
    schedule = load_payload("cubs_2025_schedule")
    remove_schedule_score(schedule, 776704, "home")
    client = FakeMlb(
        team_stats=build_team_stats(load_payload("cubs_2025_hitting_game_log")),
        schedule=build_schedule(schedule),
    )
    line = by_game_pk(collect(client))[776704]
    assert (line.hits, line.runs) == (6, 4)


def test_missing_stat_line_raises_normalization_error() -> None:
    """``HittingGameLog.stat`` is optional, so a split can arrive without one."""
    team_stats = build_team_stats(load_payload("cubs_2025_hitting_game_log"))
    game_log = team_stats["hitting"]["gameLog"]
    game_log.splits[0] = game_log.splits[0].model_copy(update={"stat": None})
    client = FakeMlb(
        team_stats=team_stats,
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError, match="776704"):
        collect(client)


def test_unexpected_split_type_raises_data_error() -> None:
    team_stats = build_team_stats(load_payload("cubs_2025_hitting_game_log"))
    team_stats["hitting"]["gameLog"].splits.append("not a split")
    client = FakeMlb(
        team_stats=team_stats,
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError, match="Unexpected"):
        collect(client)


def test_schedule_entry_without_teams_raises_normalization_error() -> None:
    payload = load_payload("cubs_2025_schedule")
    for schedule_date in payload["dates"]:
        for game in schedule_date["games"]:
            game.pop("teams")
    client = FakeMlb(
        team_stats=build_team_stats(load_payload("cubs_2025_hitting_game_log")),
        schedule=build_schedule(payload),
    )
    with pytest.raises(TeamGameDataError, match="schedule team information"):
        collect(client)


def test_missing_hits_value_raises_normalization_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    del payload["stats"][0]["splits"][0]["stat"]["hits"]
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError, match="776704"):
        collect(client)


def test_negative_source_hits_raise_normalization_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    payload["stats"][0]["splits"][0]["stat"]["hits"] = -1
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError, match="776704"):
        collect(client)


def test_unparsable_game_date_raises_normalization_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    payload["stats"][0]["splits"][0]["date"] = "08/17/2025"
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError, match="08/17/2025"):
        collect(client)


def test_game_missing_from_schedule_raises_normalization_error() -> None:
    payload = load_payload("cubs_2025_schedule")
    payload["dates"] = [
        date for date in payload["dates"] if date["date"] != "2025-08-17"
    ]
    client = FakeMlb(
        team_stats=build_team_stats(load_payload("cubs_2025_hitting_game_log")),
        schedule=build_schedule(payload),
    )
    with pytest.raises(TeamGameDataError, match="776704"):
        collect(client)


def test_game_without_the_selected_team_raises_normalization_error() -> None:
    payload = load_payload("cubs_2025_schedule")
    for schedule_date in payload["dates"]:
        for game in schedule_date["games"]:
            game["teams"]["home"]["team"]["id"] = 999
    client = FakeMlb(
        team_stats=build_team_stats(load_payload("cubs_2025_hitting_game_log")),
        schedule=build_schedule(payload),
    )
    with pytest.raises(TeamGameDataError, match="does not appear"):
        collect(client)


def test_unknown_team_raises_team_not_found() -> None:
    with pytest.raises(TeamNotFoundError, match="9999"):
        get_team_game_batting_lines(9999, SEASON, client=FakeMlb(team=None))


def test_non_mlb_team_raises_team_not_found() -> None:
    minor_league_team = Team(
        id=419,
        link="/api/v1/teams/419",
        name="Hillsboro Hops",
        sport={"id": 13, "link": "/api/v1/sports/13", "name": "High-A"},
    )
    client = FakeMlb(team=minor_league_team)
    with pytest.raises(TeamNotFoundError, match="Major League Baseball"):
        get_team_game_batting_lines(419, SEASON, client=client)


def test_empty_game_log_raises_data_error() -> None:
    client = FakeMlb(
        team_stats={}, schedule=build_schedule(load_payload("cubs_2025_schedule"))
    )
    with pytest.raises(TeamGameDataError, match="hitting game log"):
        collect(client)


def test_game_log_without_splits_raises_data_error() -> None:
    empty_game_log = Stat(group="hitting", type="gameLog", totalSplits=0, splits=[])
    client = FakeMlb(
        team_stats={"hitting": {"gameLog": empty_game_log}},
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError, match="is empty"):
        collect(client)


def test_missing_schedule_raises_data_error() -> None:
    client = FakeMlb(
        team_stats=build_team_stats(load_payload("cubs_2025_hitting_game_log")),
        schedule=None,
    )
    with pytest.raises(TeamGameDataError, match="schedule"):
        collect(client)


UPSTREAM_ERRORS = [
    MlbTransportError("Request failed"),
    MlbTimeoutError("Request failed"),
    MlbDecodeError("Bad JSON in response"),
    MlbHttpError(500, "Internal Server Error", "https://statsapi.mlb.com"),
    TheMlbStatsApiException("boom"),
]


@pytest.mark.parametrize("error", UPSTREAM_ERRORS)
def test_upstream_team_stats_errors_are_translated(error: Exception) -> None:
    client = FakeMlb(
        team_stats=error,
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameLogError) as excinfo:
        collect(client)
    assert excinfo.value.__cause__ is error
    assert not isinstance(excinfo.value, TeamGameDataError)


@pytest.mark.parametrize("error", UPSTREAM_ERRORS)
def test_upstream_schedule_errors_are_translated(error: Exception) -> None:
    client = FakeMlb(
        team_stats=build_team_stats(load_payload("cubs_2025_hitting_game_log")),
        schedule=error,
    )
    with pytest.raises(TeamGameLogError) as excinfo:
        collect(client)
    assert excinfo.value.__cause__ is error


def test_upstream_team_lookup_errors_are_translated() -> None:
    error = MlbTransportError("Request failed")
    with pytest.raises(TeamGameLogError) as excinfo:
        get_team_game_batting_lines(CUBS_ID, SEASON, client=FakeMlb(team=error))
    assert excinfo.value.__cause__ is error


def test_team_lookup_requests_the_selected_season() -> None:
    client = make_client()
    collect(client)
    assert client.calls["get_team"] == {"team_id": CUBS_ID, "season": SEASON}


def test_historical_team_name_is_used_for_every_line() -> None:
    """The lookup name wins over the game log's current franchise name."""
    historical_team = Team(
        id=CUBS_ID,
        link="/api/v1/teams/112",
        name="Chicago Orphans",
        sport=MLB_SPORT,
    )
    client = FakeMlb(
        team=historical_team,
        team_stats=build_team_stats(load_payload("cubs_2025_hitting_game_log")),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    lines = collect(client)
    assert {line.team_name for line in lines} == {"Chicago Orphans"}


def test_only_regular_season_data_is_requested() -> None:
    client = make_client()
    collect(client)
    assert client.calls["get_team_stats"] == {
        "team_id": CUBS_ID,
        "stats": ["gameLog"],
        "groups": ["hitting"],
        "season": SEASON,
        "gameType": "R",
    }
    assert client.calls["get_schedule"] == {
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "sport_id": 1,
        "team_id": CUBS_ID,
        "gameTypes": "R",
    }


def test_retrieval_uses_three_upstream_calls() -> None:
    client = make_client()
    collect(client)
    assert sorted(client.calls) == ["get_schedule", "get_team", "get_team_stats"]


def test_owned_client_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client()
    monkeypatch.setattr(team_game_logs, "Mlb", lambda: client)
    assert len(get_team_game_batting_lines(CUBS_ID, SEASON)) == 6
    assert client.closed is True


def test_supplied_client_is_not_closed() -> None:
    client = make_client()
    collect(client)
    assert client.closed is False


REMOVE_FIELD = object()


def client_with_strikeouts(game_pk: int, value: Any) -> FakeMlb:
    """Build a client whose hitting game log has a modified ``strikeOuts``.

    Pass ``REMOVE_FIELD`` to drop the key entirely, or ``None`` to keep the key
    with a JSON null.
    """
    payload = load_payload("cubs_2025_hitting_game_log")
    for split in payload["stats"][0]["splits"]:
        if split["game"]["gamePk"] == game_pk:
            if value is REMOVE_FIELD:
                split["stat"].pop("strikeOuts", None)
            else:
                split["stat"]["strikeOuts"] = value
    return FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )


def test_batting_strikeouts_are_read_from_the_hitting_game_log() -> None:
    """``strikeOuts`` in the payload reaches the domain record unchanged."""
    lines = by_game_pk(collect(make_client()))
    assert [
        (pk, lines[pk].strikeouts)
        for pk in (776704, 777459, 776691, 776676, 776640, 776618)
    ] == [(776704, 5), (777459, 8), (776691, 9), (776676, 9), (776640, 10), (776618, 6)]


def test_batting_strikeouts_belong_to_the_selected_team() -> None:
    """The 12-1 win at the Angels: hits, runs, and strikeouts must agree."""
    line = by_game_pk(collect(make_client()))[776618]
    assert (line.hits, line.runs, line.strikeouts) == (14, 12, 6)


def test_strikeouts_are_collected_without_an_extra_mlb_request() -> None:
    """Strikeouts ride along on the requests Milestone 1 already made."""
    client = make_client()
    lines = collect(client)
    assert all(line.strikeouts is not None for line in lines)
    assert sorted(client.calls) == ["get_schedule", "get_team", "get_team_stats"]


def test_the_hitting_game_log_is_requested_once_for_all_metrics() -> None:
    client = make_client()
    collect(client)
    assert client.calls["get_team_stats"]["stats"] == ["gameLog"]
    assert client.calls["get_team_stats"]["groups"] == ["hitting"]


def test_absent_strikeouts_field_raises_data_error() -> None:
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client_with_strikeouts(776704, REMOVE_FIELD))
    message = str(excinfo.value)
    assert "strikeOuts" in message
    assert "776704" in message


def test_null_strikeouts_raise_data_error() -> None:
    """An explicit JSON null is as unusable as an absent field."""
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client_with_strikeouts(776704, None))
    message = str(excinfo.value)
    assert "strikeOuts" in message
    assert "776704" in message


def test_negative_strikeouts_raise_data_error() -> None:
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client_with_strikeouts(776704, -3))
    message = str(excinfo.value)
    assert "negative" in message
    assert "776704" in message


def test_missing_strikeouts_are_never_substituted_with_zero() -> None:
    """The whole import fails rather than inventing a strikeout-free game."""
    with pytest.raises(TeamGameDataError):
        collect(client_with_strikeouts(776640, REMOVE_FIELD))


def test_zero_strikeouts_is_kept_as_a_real_value() -> None:
    line = by_game_pk(collect(client_with_strikeouts(776704, 0)))[776704]
    assert line.strikeouts == 0


def test_hits_and_runs_still_normalize_alongside_strikeouts() -> None:
    """Milestone 1 and 2 behaviour must survive the added field."""
    line = by_game_pk(collect(make_client()))[776704]
    assert (line.hits, line.runs, line.strikeouts) == (6, 4, 5)


def test_conflicting_duplicate_strikeouts_raise_data_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    splits = payload["stats"][0]["splits"]
    conflicting = copy.deepcopy(splits[0])
    conflicting["stat"]["strikeOuts"] = 12
    splits.append(conflicting)
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    assert "strikeouts 5 vs 12" in str(excinfo.value)


# ------------------------------------------------------- baserunner components


def client_with_stat_field(game_pk: int, field: str, value: Any) -> FakeMlb:
    """Build a client whose hitting game log has one modified stat field.

    Pass ``REMOVE_FIELD`` to drop the key entirely, or ``None`` to keep the key
    with a JSON null.
    """
    payload = load_payload("cubs_2025_hitting_game_log")
    for split in payload["stats"][0]["splits"]:
        if split["game"]["gamePk"] == game_pk:
            if value is REMOVE_FIELD:
                split["stat"].pop(field, None)
            else:
                split["stat"][field] = value
    return FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )


def test_walks_and_hit_by_pitch_are_read_from_the_hitting_game_log() -> None:
    line = by_game_pk(collect(make_client()))[776618]
    assert (line.base_on_balls, line.hit_by_pitch) == (3, 1)


def test_baserunner_components_are_collected_without_an_extra_mlb_request() -> None:
    client = make_client()
    lines = collect(client)
    assert all(line.base_on_balls is not None for line in lines)
    assert all(line.hit_by_pitch is not None for line in lines)
    assert sorted(client.calls) == ["get_schedule", "get_team", "get_team_stats"]


@pytest.mark.parametrize("field", ["baseOnBalls", "hitByPitch"])
def test_absent_baserunner_field_raises_data_error(field: str) -> None:
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client_with_stat_field(776704, field, REMOVE_FIELD))
    message = str(excinfo.value)
    assert field in message
    assert "776704" in message


@pytest.mark.parametrize("field", ["baseOnBalls", "hitByPitch"])
def test_null_baserunner_field_raises_data_error(field: str) -> None:
    """An explicit JSON null is as unusable as an absent field."""
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client_with_stat_field(776704, field, None))
    message = str(excinfo.value)
    assert field in message
    assert "776704" in message


@pytest.mark.parametrize("field", ["baseOnBalls", "hitByPitch"])
def test_negative_baserunner_field_raises_data_error(field: str) -> None:
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client_with_stat_field(776704, field, -2))
    message = str(excinfo.value)
    assert "negative" in message
    assert "776704" in message


def test_zero_walks_is_kept_as_a_real_value() -> None:
    line = by_game_pk(collect(client_with_stat_field(776704, "baseOnBalls", 0)))[776704]
    assert line.base_on_balls == 0


def test_conflicting_duplicate_walks_raise_data_error() -> None:
    payload = load_payload("cubs_2025_hitting_game_log")
    splits = payload["stats"][0]["splits"]
    conflicting = copy.deepcopy(splits[0])
    conflicting["stat"]["baseOnBalls"] = 99
    splits.append(conflicting)
    client = FakeMlb(
        team_stats=build_team_stats(payload),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client)
    assert "base_on_balls 5 vs 99" in str(excinfo.value)


def drop_game_log_splits(payload: dict[str, Any], *game_pks: int) -> dict[str, Any]:
    """Copy a game log payload with the named games' splits removed.

    This reproduces the upstream shape the package can produce on its own:
    ``python-mlb-statsapi`` drops a split whose raw stat object is empty, so a
    completed game can be absent from the parsed game log even though the
    schedule still lists it.
    """
    copied = copy.deepcopy(payload)
    dropped = set(game_pks)
    for stat_group in copied["stats"]:
        stat_group["splits"] = [
            split
            for split in stat_group["splits"]
            if split["game"]["gamePk"] not in dropped
        ]
    return copied


def client_missing(*game_pks: int) -> FakeMlb:
    """A Cubs client whose game log is missing the named completed games."""
    return FakeMlb(
        team_stats=build_team_stats(
            drop_game_log_splits(load_payload("cubs_2025_hitting_game_log"), *game_pks)
        ),
        schedule=build_schedule(load_payload("cubs_2025_schedule")),
    )


def test_every_completed_scheduled_game_is_represented() -> None:
    """The captured season is whole, so the completeness check must not fire."""
    lines = collect(make_client())
    completed_schedule_pks = {776704, 777459, 776691, 776676, 776640, 776618}
    assert {line.game_pk for line in lines} == completed_schedule_pks


def test_completed_game_missing_from_the_game_log_raises_data_error() -> None:
    """A short team-season is refused rather than returned one game light."""
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client_missing(776640))
    message = str(excinfo.value)
    assert "776640" in message
    assert str(CUBS_ID) in message
    assert str(SEASON) in message
    assert "hitting game log" in message


def test_multiple_missing_completed_games_are_all_named() -> None:
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client_missing(776640, 776704))
    message = str(excinfo.value)
    assert "776640" in message
    assert "776704" in message
    assert "2 completed regular-season games" in message


def test_missing_completed_game_is_not_replaced_by_a_zeroed_record() -> None:
    """No fabricated batting line may stand in for data MLB did not return."""
    with pytest.raises(TeamGameDataError):
        collect(client_missing(776640))


def test_non_completed_schedule_games_absent_from_the_game_log_do_not_fail() -> None:
    """Cancelled and postponed games are never expected in the game log."""
    lines = get_team_game_batting_lines(
        CUBS_ID,
        SEASON,
        client=make_client(
            game_log="edge_cases_hitting_game_log",
            schedule="edge_cases_schedule",
        ),
    )
    assert [line.game_pk for line in lines] == [776500]


def test_postponed_and_made_up_rows_count_as_one_required_game() -> None:
    """776691 appears twice in the schedule: postponed, then completed."""
    lines = collect(make_client())
    assert sum(1 for line in lines if line.game_pk == 776691) == 1


def test_missing_made_up_game_is_reported_once_not_twice() -> None:
    """The duplicated schedule rows must not inflate the missing-game count."""
    with pytest.raises(TeamGameDataError) as excinfo:
        collect(client_missing(776691))
    message = str(excinfo.value)
    assert "1 completed regular-season game " in message
    assert message.count("776691") == 1


def test_completeness_check_adds_no_upstream_request() -> None:
    """The check reuses the schedule already fetched for normalization."""
    client = client_missing(776640)
    with pytest.raises(TeamGameDataError):
        collect(client)
    assert set(client.calls) == {"get_team", "get_team_stats", "get_schedule"}
