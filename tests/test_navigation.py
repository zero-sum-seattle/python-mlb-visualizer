"""Tests for navigation between the analytics pages.

Issue #25 added a fourth entry, the baserunners page added a fifth, issue #39
added run differential as a sixth, issue #41 added pitching as a seventh, and
issue #43 added hits allowed as an eighth.
The list is asserted in full rather than by membership, so a page added without
a route, or a route added without a link, fails here.
"""

from app.web.navigation import (
    BASERUNNERS_PATH,
    COMPARISON_PATH,
    HITS_ALLOWED_PATH,
    HITS_PATH,
    PITCHING_PATH,
    RUN_DIFFERENTIAL_PATH,
    RUNS_PATH,
    STRIKEOUTS_PATH,
    build_nav_links,
)


def test_every_metric_page_is_linked() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert [link.label for link in links] == [
        "Hits",
        "Batting Strikeouts",
        "Runs",
        "Baserunners",
        "Run Differential",
        "Pitching",
        "Hits Allowed",
        "Comparison",
    ]


def test_links_point_at_real_routes() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert [link.href for link in links] == [
        "/",
        "/strikeouts",
        "/runs",
        "/baserunners",
        "/run-differential",
        "/pitching",
        "/hits-allowed",
        "/comparison",
    ]


def test_the_current_page_is_marked() -> None:
    links = build_nav_links(current_path=STRIKEOUTS_PATH)
    assert [link.is_current for link in links] == [
        False,
        True,
        False,
        False,
        False,
        False,
        False,
        False,
    ]


def test_the_runs_page_can_be_the_current_one() -> None:
    links = build_nav_links(current_path=RUNS_PATH)
    assert [link.is_current for link in links] == [
        False,
        False,
        True,
        False,
        False,
        False,
        False,
        False,
    ]


def test_the_baserunners_page_can_be_the_current_one() -> None:
    links = build_nav_links(current_path=BASERUNNERS_PATH)
    assert [link.is_current for link in links] == [
        False,
        False,
        False,
        True,
        False,
        False,
        False,
        False,
    ]


def test_the_run_differential_page_can_be_the_current_one() -> None:
    links = build_nav_links(current_path=RUN_DIFFERENTIAL_PATH)
    assert [link.is_current for link in links] == [
        False,
        False,
        False,
        False,
        True,
        False,
        False,
        False,
    ]


def test_the_pitching_page_can_be_the_current_one() -> None:
    links = build_nav_links(current_path=PITCHING_PATH)
    assert [link.is_current for link in links] == [
        False,
        False,
        False,
        False,
        False,
        True,
        False,
        False,
    ]


def test_the_comparison_page_can_be_the_current_one() -> None:
    links = build_nav_links(current_path=COMPARISON_PATH)
    assert [link.is_current for link in links] == [
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
    ]


def test_only_one_page_is_current_at_a_time() -> None:
    for path in (
        HITS_PATH,
        STRIKEOUTS_PATH,
        RUNS_PATH,
        BASERUNNERS_PATH,
        RUN_DIFFERENTIAL_PATH,
        PITCHING_PATH,
        HITS_ALLOWED_PATH,
        COMPARISON_PATH,
    ):
        links = build_nav_links(current_path=path)
        assert sum(link.is_current for link in links) == 1


def test_selection_is_carried_between_pages() -> None:
    links = build_nav_links(current_path=HITS_PATH, team_id=136, season=2025, window=15)
    assert links[1].href == "/strikeouts?team_id=136&season=2025&window=15"
    assert links[2].href == "/runs?team_id=136&season=2025&window=15"
    assert links[3].href == "/baserunners?team_id=136&season=2025&window=15"
    assert links[4].href == "/run-differential?team_id=136&season=2025&window=15"
    assert links[5].href == "/pitching?team_id=136&season=2025&window=15"
    assert links[6].href == "/hits-allowed?team_id=136&season=2025&window=15"
    assert links[7].href == "/comparison?team_id=136&season=2025&window=15"


def test_no_selection_produces_plain_paths() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert [link.href for link in links] == [
        "/",
        "/strikeouts",
        "/runs",
        "/baserunners",
        "/run-differential",
        "/pitching",
        "/hits-allowed",
        "/comparison",
    ]


def test_unset_values_are_left_out_of_the_query() -> None:
    links = build_nav_links(current_path=HITS_PATH, team_id=136, window=30)
    assert links[1].href == "/strikeouts?team_id=136&window=30"
    assert links[2].href == "/runs?team_id=136&window=30"
    assert links[3].href == "/baserunners?team_id=136&window=30"
    assert links[4].href == "/run-differential?team_id=136&window=30"
    assert links[5].href == "/pitching?team_id=136&window=30"
    assert links[6].href == "/hits-allowed?team_id=136&window=30"
    assert links[7].href == "/comparison?team_id=136&window=30"
