"""Tests for navigation between the metric pages.

Issue #24 added a third entry. The list is asserted in full rather than by
membership, so a page added without a route, or a route added without a link,
fails here.
"""

from app.web.navigation import (
    HITS_PATH,
    RUNS_PATH,
    STRIKEOUTS_PATH,
    build_nav_links,
)


def test_every_metric_page_is_linked() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert [link.label for link in links] == ["Hits", "Batting Strikeouts", "Runs"]


def test_links_point_at_real_routes() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert [link.href for link in links] == ["/", "/strikeouts", "/runs"]


def test_the_current_page_is_marked() -> None:
    links = build_nav_links(current_path=STRIKEOUTS_PATH)
    assert [link.is_current for link in links] == [False, True, False]


def test_the_runs_page_can_be_the_current_one() -> None:
    links = build_nav_links(current_path=RUNS_PATH)
    assert [link.is_current for link in links] == [False, False, True]


def test_only_one_page_is_current_at_a_time() -> None:
    for path in (HITS_PATH, STRIKEOUTS_PATH, RUNS_PATH):
        links = build_nav_links(current_path=path)
        assert sum(link.is_current for link in links) == 1


def test_selection_is_carried_between_pages() -> None:
    links = build_nav_links(current_path=HITS_PATH, team_id=136, season=2025, window=15)
    assert links[1].href == "/strikeouts?team_id=136&season=2025&window=15"
    assert links[2].href == "/runs?team_id=136&season=2025&window=15"


def test_no_selection_produces_plain_paths() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert [link.href for link in links] == ["/", "/strikeouts", "/runs"]


def test_unset_values_are_left_out_of_the_query() -> None:
    links = build_nav_links(current_path=HITS_PATH, team_id=136, window=30)
    assert links[1].href == "/strikeouts?team_id=136&window=30"
    assert links[2].href == "/runs?team_id=136&window=30"
