"""Period parsing, link derivation, and month-to-page routing."""
import datetime as dt

import pytest

from dotbo.catalog import Catalog, ListingPage, month_window
from dotbo.links import id_hash, page_url, parse_id_hash
from dotbo.period import OPEN, RANGE, SINGLE, UNKNOWN, YEAR, parse_period


# ---------------------------------------------------------------- periods

@pytest.mark.parametrize(
    "title,kind,start,end",
    [
        # Every shape actually present in the Data Services category today.
        ("... court orders (July – Sept 2026)", RANGE, (2026, 7, 1), (2026, 9, 30)),
        ("... court orders (Apr – Jun 2026)", RANGE, (2026, 4, 1), (2026, 6, 30)),
        ("Termination Letters (June-Sep 2026)", RANGE, (2026, 6, 1), (2026, 9, 30)),
        ("Termination Letters (Oct to Dec 2023)", RANGE, (2023, 10, 1), (2023, 12, 31)),
        ("Termination Letters (Jan-May 2026)", RANGE, (2026, 1, 1), (2026, 5, 31)),
        ("Termination Letters (Jan to June 2024)", RANGE, (2024, 1, 1), (2024, 6, 30)),
        ("Termination Letters (May 2025)", SINGLE, (2025, 5, 1), (2025, 5, 31)),
        ("Termination Letters (June 2025-II)", SINGLE, (2025, 6, 1), (2025, 6, 30)),
        ("... court orders (older than 2025)", OPEN, None, (2024, 12, 31)),
        ("... court orders (older than 2025 – 2)", OPEN, None, (2024, 12, 31)),
        # Shapes DoT has not used yet but plausibly will.
        ("... court orders (Oct - Dec 2026)", RANGE, (2026, 10, 1), (2026, 12, 31)),
        ("... court orders (October to December 2026)", RANGE, (2026, 10, 1), (2026, 12, 31)),
        ("... court orders (Nov 2026 - Jan 2027)", RANGE, (2026, 11, 1), (2027, 1, 31)),
        ("... court orders (Jan & Feb 2027)", RANGE, (2027, 1, 1), (2027, 2, 28)),
        ("... court orders (2027)", YEAR, (2027, 1, 1), (2027, 12, 31)),
    ],
)
def test_parse_period(title, kind, start, end):
    p = parse_period(title)
    assert p.kind == kind
    assert p.start == (dt.date(*start) if start else None)
    assert p.end == (dt.date(*end) if end else None)


def test_unparseable_title_is_unknown_and_matches_everything():
    p = parse_period("Blocking Notifications to Internet Service Licensees")
    assert p.kind == UNKNOWN
    assert p.covers(2026, 11) and p.covers(2019, 3)


def test_quarter_covers_only_its_own_months():
    p = parse_period("(July – Sept 2026)")
    assert p.covers(2026, 8) and p.covers(2026, 9)
    assert not p.covers(2026, 6) and not p.covers(2026, 10)


def test_open_period_is_bounded_at_the_top():
    p = parse_period("(older than 2025)")
    assert p.covers(2024, 12) and not p.covers(2026, 8)


# ---------------------------------------------------------------- links

@pytest.mark.parametrize(
    "post_id,suffix", [(75697, "cTO2UzNtQWa"), (51032, "IzMwETNtQWa"), (51819, "kTM4ETNtQWa")]
)
def test_public_url_hash_matches_the_real_site(post_id, suffix):
    assert id_hash(post_id) == suffix
    assert parse_id_hash(suffix) == post_id


def test_page_url_reproduces_a_real_link():
    assert page_url(
        75697,
        "blocking-notifications-instructions-to-internet-service-licensees-under-court-orders-july-sept-2026",
        "Blocking Notifications/instructions to Internet Service Licensees under court orders (July – Sept 2026)",
    ) == (
        "https://www.dot.gov.in/documents/orders-and-notices/"
        "blocking-notifications-instructions-to-internet-service-licensees-under-court-orders-july-sept-2026"
        "-cTO2UzNtQWa?pageTitle=Blocking-Notifications/instructions-to-Internet-Service-Licensees-"
        "under-court-orders-(July-Sept-2026)"
    )


def test_slug_drops_apostrophes_rather_than_hyphenating_them():
    """WordPress turns "Hon'ble" into "honble" - the difference between a
    working dot.gov.in link and a broken one."""
    from dotbo.catalog import _slugify

    assert _slugify(
        "CS COMM 740 of 2026 in the Hon\u2019ble High Court of Delhi: "
        "DoT instructions dated 24.08.2026 to ISPs"
    ) == (
        "cs-comm-740-of-2026-in-the-honble-high-court-of-delhi-"
        "dot-instructions-dated-24-08-2026-to-isps"
    )


# ---------------------------------------------------------------- routing

def _page(page_id, title, first="", last=""):
    return ListingPage(
        page_id=page_id, title=title, slug="s", period=parse_period(title),
        observed_first=first, observed_last=last,
    )


def _rank(pages, year, month):
    catalog = Catalog.__new__(Catalog)
    catalog._pages = pages
    return {p.page_id: score for score, p in Catalog.rank_pages(catalog, year, month)}


REAL_PAGES = [
    _page(75697, "... (July – Sept 2026)", "2026-07-02", "2026-09-02"),
    _page(51032, "... (Apr – Jun 2026)", "2026-04-28", "2026-06-30"),
    _page(51715, "... (older than 2025)", "2025-01-07", "2025-07-31"),
    _page(51819, "... (older than 2025 – 2)", "2023-12-06", "2025-12-30"),
]


def test_august_2026_routes_to_the_july_september_page():
    scores = _rank(REAL_PAGES, 2026, 8)
    assert scores[75697] == 3
    assert scores[51032] == 0


def test_may_2025_reaches_the_misleadingly_titled_pages():
    """Both "older than 2025" pages in fact hold 2025 orders."""
    scores = _rank(REAL_PAGES, 2025, 5)
    assert scores[51715] >= 1 and scores[51819] >= 1
    assert scores[75697] == 0


def test_a_future_quarter_page_is_picked_up_for_its_months():
    pages = REAL_PAGES + [_page(90000, "... (Oct - Dec 2026)")]
    scores = _rank(pages, 2026, 11)
    assert scores[90000] == 3
    assert scores[75697] == 0


def test_september_also_considers_the_next_quarter_for_late_publication():
    """An order dated 30 September is published in October, on the next page."""
    pages = REAL_PAGES + [_page(90000, "... (Oct - Dec 2026)")]
    assert _rank(pages, 2026, 9)[90000] == 2


def test_the_newest_page_stays_in_play_before_the_next_one_exists():
    """DoT appends to the current page until it creates the new period page."""
    assert _rank(REAL_PAGES, 2026, 11)[75697] == 1


def test_month_window_runs_past_the_month_end():
    start, end = month_window(2026, 8, look_ahead_days=31)
    assert start == dt.date(2026, 8, 1)
    assert end == dt.date(2026, 10, 2)
