"""Tests for navigation between the metric pages."""

from app.web.navigation import (
    HITS_PATH,
    STRIKEOUTS_PATH,
    build_nav_links,
)


def test_both_metric_pages_are_linked() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert [link.label for link in links] == ["Hits", "Batting Strikeouts"]


def test_links_point_at_real_routes() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert [link.href for link in links] == ["/", "/strikeouts"]


def test_the_current_page_is_marked() -> None:
    links = build_nav_links(current_path=STRIKEOUTS_PATH)
    assert [link.is_current for link in links] == [False, True]


def test_only_one_page_is_current_at_a_time() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert sum(link.is_current for link in links) == 1


def test_selection_is_carried_between_pages() -> None:
    links = build_nav_links(current_path=HITS_PATH, team_id=136, season=2025, window=15)
    assert links[1].href == "/strikeouts?team_id=136&season=2025&window=15"


def test_no_selection_produces_plain_paths() -> None:
    links = build_nav_links(current_path=HITS_PATH)
    assert [link.href for link in links] == ["/", "/strikeouts"]


def test_unset_values_are_left_out_of_the_query() -> None:
    links = build_nav_links(current_path=HITS_PATH, team_id=136, window=30)
    assert links[1].href == "/strikeouts?team_id=136&window=30"
