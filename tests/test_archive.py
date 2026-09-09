"""Library storage, back-catalogue scheduling, and the search query language."""
import datetime as dt

import pytest

from dotbo import backfill, library
from dotbo.search import Index, Record, normalise_case, parse_query


# ---------------------------------------------------------------- library

def test_slug_and_label():
    assert library.slug_for(2026, 8) == "2026-08"
    assert library.label_for(2026, 8) == "Blocking Orders_August 2026"


def test_stored_month_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY", tmp_path)

    class _Att:
        att_id, pdf_url, filesize = 42, "https://example.gov.in/x.pdf", 10

    class _Ex:
        declared_count, corroborated = 3, True
        corroboration, source, ocr_engine = "matches the 3 declared", "annexure", ""

    class _Letter:
        case_no, court, action, subject = "CS(COMM) 1/2026", "High Court of Delhi", "block", "X v Y"

    class _Order:
        index, filename = 1, "1_Blocking order dated 05 August 2026.pdf"
        attachment, extraction, letter = _Att(), _Ex(), _Letter()
        order_date = dt.date(2026, 8, 5)
        urls = ["a.example.com", "b.example.com", "c.example.com"]

    class _Options:
        year, month, days = 2026, 8, ()

    class _Result:
        options, orders = _Options(), [_Order()]
        docx_path = report_html = report_csv = None

    stored = library.save(_Result())
    assert stored.order_count == 1 and stored.url_count == 3 and stored.corroborated_count == 1

    reloaded = library.load("2026-08")
    assert reloaded is not None
    assert reloaded.label == "Blocking Orders_August 2026"
    assert reloaded.orders[0].urls == ["a.example.com", "b.example.com", "c.example.com"]
    assert reloaded.orders[0].case_no == "CS(COMM) 1/2026"
    assert library.has(2026, 8) and not library.has(2026, 9)
    assert [m.slug for m in library.entries()] == ["2026-08"]


# ---------------------------------------------------------------- backfill

def test_months_between_spans_year_ends():
    assert backfill.months_between((2026, 11), (2027, 2)) == [
        (2026, 11), (2026, 12), (2027, 1), (2027, 2)
    ]


def test_pending_skips_what_is_already_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY", tmp_path)
    (tmp_path / "2024-01").mkdir()
    (tmp_path / "2024-01" / "manifest.json").write_text("{}")
    months = backfill.pending((2023, 12), (2024, 3))
    assert (2024, 1) not in months
    assert months == [(2023, 12), (2024, 2), (2024, 3)]


# ---------------------------------------------------------------- queries

def test_bare_words_and_field_prefixes():
    q = parse_query('vegamovies url:crichd case:"CS(COMM) 331" court:delhi action:unblock')
    assert q.words == ["vegamovies"]
    assert q.url == ["crichd"] and q.case == ["cs(comm) 331"]
    assert q.court == ["delhi"] and q.action == ["unblock"]


@pytest.mark.parametrize(
    "value,first,last",
    [
        ("2026", (2026, 1, 1), (2026, 12, 31)),
        ("2026-08", (2026, 8, 1), (2026, 8, 31)),
        ("2026-08-05", (2026, 8, 5), (2026, 8, 5)),
        ("2026-08-05..2026-08-31", (2026, 8, 5), (2026, 8, 31)),
    ],
)
def test_date_queries(value, first, last):
    q = parse_query(f"date:{value}")
    assert q.date_from == dt.date(*first)
    assert q.date_to == dt.date(*last)


def test_case_numbers_match_regardless_of_punctuation():
    """DoT writes the same case as "CS(COMM) 331/2025" and "CS COMM 331 of 2025"."""
    assert normalise_case("CS(COMM) 331") in normalise_case("CS COMM 331 of 2025")


def _index(records):
    ix = Index.__new__(Index)
    ix.records = [r.finalise() for r in records]
    ix.built_months = []
    return ix


RECORDS = [
    Record(attachment_id=1, tier="built", title="CS COMM 740 of 2026 ... Delhi",
           case_no="CS(COMM) 740 of 2026", court="High Court of Delhi", action="block",
           order_date="2026-08-24", month_slug="2026-08", index=17,
           urls=["vegamovies.condos", "https://vegamovies.diamonds", "other.example"]),
    Record(attachment_id=2, tier="built", title="CS COMM 420 of 2023 ... Delhi",
           case_no="CS(COMM) 420/2023", court="High Court of Delhi", action="unblock",
           order_date="2026-08-07", month_slug="2026-08", index=6,
           urls=["www.asianpaintsbbs.com"]),
    Record(attachment_id=3, tier="catalogued", title="Writ-A No. 30480 of 2026 ... Allahabad",
           case_no="Writ-A No. 30480 of 2026", court="High Court of Judicature at Allahabad",
           order_date="2026-09-02"),
]


def test_url_search_returns_only_the_matching_urls():
    hits = _index(RECORDS).search("url:vegamovies")
    assert len(hits) == 1
    assert hits[0].matched_urls == ["vegamovies.condos", "https://vegamovies.diamonds"]


def test_action_and_month_narrow_together():
    hits = _index(RECORDS).search("month:2026-08 action:unblock")
    assert [h.record.attachment_id for h in hits] == [2]


def test_case_query_ignores_punctuation():
    assert len(_index(RECORDS).search('case:"CS COMM 740"')) == 1


def test_date_range_filters():
    hits = _index(RECORDS).search("date:2026-08")
    assert {h.record.attachment_id for h in hits} == {1, 2}


def test_order_number_within_a_compilation():
    hits = _index(RECORDS).search("order:6 month:2026-08")
    assert [h.record.attachment_id for h in hits] == [2]


def test_a_catalogued_record_is_still_findable_without_urls():
    hits = _index(RECORDS).search("allahabad")
    assert [h.record.tier for h in hits] == ["catalogued"]


def test_empty_query_returns_nothing():
    assert _index(RECORDS).search("   ") == []
