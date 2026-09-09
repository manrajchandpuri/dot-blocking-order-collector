"""Search across every order DoT has published, and every URL ever blocked.

The question this exists to answer is *"has this domain been blocked before, and
under which order?"* - followed closely by "where is the DoT page for that
quarter?".

Two tiers, and the UI says which one a result came from:

* **Catalogued** - every order in ``workspace/index.json``, ~475 of them, with no
  downloads at all: title, case number, court, publication date, the PDF link
  and the reconstructed public page link.
* **Built** - months present in the library, which add the order's real date,
  block vs unblock, and every URL extracted from it.

At this size - a few hundred orders and roughly eighteen thousand URLs - a
lowercase scan takes well under a millisecond, so there is no index format, no
SQLite and no schema to keep in step with the extractor.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from . import library
from .catalog import Catalog
from .links import page_url

CATALOGUED = "catalogued"
BUILT = "built"

FIELDS = ("url", "case", "court", "party", "date", "action", "month", "order", "text")

_TOKEN = re.compile(
    r'(?P<field>[a-z]+):(?P<quoted>"[^"]*"|\S+)|(?P<quoted_bare>"[^"]*")|(?P<word>\S+)',
    re.I,
)


@dataclass
class Record:
    """One order, flattened for searching."""

    attachment_id: int
    tier: str
    title: str = ""
    case_no: str = ""
    court: str = ""
    subject: str = ""
    case_key: str = ""      # case number with punctuation stripped, for matching
    action: str = ""
    order_date: str = ""
    published: str = ""
    pdf_url: str = ""
    public_url: str = ""
    month_slug: str = ""
    month_label: str = ""
    index: int = 0
    filename: str = ""
    urls: list[str] = field(default_factory=list)
    listing_title: str = ""
    listing_url: str = ""

    @property
    def date(self) -> str:
        return self.order_date or self.published

    @property
    def haystack(self) -> str:
        return self._haystack

    def finalise(self) -> "Record":
        self.case_key = normalise_case(self.case_no or self.title)
        self._haystack = " ".join(
            [self.title, self.case_no, self.court, self.subject, self.filename,
             self.listing_title, " ".join(self.urls)]
        ).lower()
        return self


@dataclass
class Hit:
    record: Record
    matched_urls: list[str] = field(default_factory=list)


@dataclass
class Query:
    """A parsed query: free words plus field constraints."""

    words: list[str] = field(default_factory=list)
    url: list[str] = field(default_factory=list)
    case: list[str] = field(default_factory=list)
    court: list[str] = field(default_factory=list)
    party: list[str] = field(default_factory=list)
    action: list[str] = field(default_factory=list)
    month: list[str] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    date_from: dt.date | None = None
    date_to: dt.date | None = None

    @property
    def empty(self) -> bool:
        return not any(
            [self.words, self.url, self.case, self.court, self.party,
             self.action, self.month, self.order, self.date_from, self.date_to]
        )


def parse_query(text: str) -> Query:
    """Free words, plus ``field:value`` constraints; quotes group a phrase."""
    query = Query()
    for m in _TOKEN.finditer(text or ""):
        if m.group("field"):
            field_name = m.group("field").lower()
            value = (m.group("quoted") or "").strip('"').lower()
            if field_name in ("url", "domain", "site"):
                query.url.append(value)
            elif field_name in ("case", "cs", "suit"):
                query.case.append(value)
            elif field_name == "court":
                query.court.append(value)
            elif field_name in ("party", "plaintiff", "subject"):
                query.party.append(value)
            elif field_name == "action":
                query.action.append(value)
            elif field_name in ("month", "compilation"):
                query.month.append(value)
            elif field_name in ("order", "no", "num"):
                query.order.append(value)
            elif field_name == "date":
                _apply_date(query, value)
            else:
                query.words.append(f"{field_name}:{value}")
        else:
            word = (m.group("quoted_bare") or m.group("word") or "").strip('"').lower()
            if word:
                query.words.append(word)
    return query


def _apply_date(query: Query, value: str) -> None:
    """``2026-08``, ``2026``, ``2026-08-05``, or ``2026-08-05..2026-08-31``."""
    if ".." in value:
        lo, _, hi = value.partition("..")
        query.date_from = _floor(lo)
        query.date_to = _ceil(hi)
        return
    query.date_from, query.date_to = _floor(value), _ceil(value)


def _floor(value: str) -> dt.date | None:
    parts = [p for p in re.split(r"[-/]", value.strip()) if p]
    try:
        if len(parts) == 1:
            return dt.date(int(parts[0]), 1, 1)
        if len(parts) == 2:
            return dt.date(int(parts[0]), int(parts[1]), 1)
        return dt.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


def _ceil(value: str) -> dt.date | None:
    import calendar

    parts = [p for p in re.split(r"[-/]", value.strip()) if p]
    try:
        if len(parts) == 1:
            return dt.date(int(parts[0]), 12, 31)
        if len(parts) == 2:
            year, month = int(parts[0]), int(parts[1])
            return dt.date(year, month, calendar.monthrange(year, month)[1])
        return dt.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- index


class Index:
    """Every known order, refreshed from disk - never from the network."""

    def __init__(self):
        self.records: list[Record] = []
        self.built_months: list[library.StoredMonth] = []
        self.reload()

    def reload(self) -> "Index":
        catalog = Catalog()
        # Cache only: searching must never reach the network.
        listings = {p.page_id: p for p in catalog.cached_pages()}
        by_id: dict[int, Record] = {}

        for att in catalog.cached_attachments():
            listing = listings.get(att.listing_id)
            by_id[att.att_id] = Record(
                attachment_id=att.att_id,
                tier=CATALOGUED,
                title=att.title,
                case_no=_case_from_title(att.title),
                court=_court_from_title(att.title),
                published=att.published.isoformat() if att.published else "",
                order_date=att.title_date.isoformat() if att.title_date else "",
                pdf_url=att.pdf_url,
                public_url=att.public_url,
                listing_title=listing.title if listing else "",
                listing_url=listing.public_url if listing else "",
            )

        self.built_months = library.entries()
        for month in self.built_months:
            for order in month.orders:
                record = by_id.get(order.attachment_id) or Record(
                    attachment_id=order.attachment_id, tier=BUILT
                )
                record.tier = BUILT
                record.case_no = order.case_no or record.case_no
                record.court = order.court or record.court
                record.subject = order.subject
                record.action = order.action
                record.order_date = order.order_date or record.order_date
                record.pdf_url = order.pdf_url or record.pdf_url
                record.urls = list(order.urls)
                record.month_slug = month.slug
                record.month_label = month.label
                record.index = order.index
                record.filename = order.filename
                by_id[order.attachment_id] = record

        self.records = [r.finalise() for r in by_id.values()]
        self.records.sort(key=lambda r: r.date or "", reverse=True)
        return self

    # ---------------------------------------------------------------- query

    def search(self, text: str, limit: int = 200) -> list[Hit]:
        query = parse_query(text)
        if query.empty:
            return []
        hits: list[Hit] = []
        for record in self.records:
            matched = self._match(record, query)
            if matched is not None:
                hits.append(Hit(record, matched))
            if len(hits) >= limit:
                break
        return hits

    @staticmethod
    def _match(record: Record, query: Query) -> list[str] | None:
        """Matched URLs if the record satisfies every constraint, else None."""
        for word in query.words:
            if word not in record.haystack:
                return None
        for value in query.case:
            # "CS(COMM) 331" must find "CS COMM 331 of 2025": compare on the
            # letters and digits alone, since DoT punctuates case numbers every
            # way imaginable.
            if normalise_case(value) not in record.case_key:
                return None
        for value in query.court:
            if value not in record.court.lower() and value not in record.title.lower():
                return None
        for value in query.party:
            if value not in record.subject.lower() and value not in record.title.lower():
                return None
        for value in query.action:
            if not record.action.startswith(value[:5].rstrip()):
                return None
        for value in query.month:
            if value not in record.month_slug and value.lower() not in record.month_label.lower():
                return None
        for value in query.order:
            if not value.isdigit() or int(value) != record.index:
                return None

        if query.date_from or query.date_to:
            try:
                when = dt.date.fromisoformat(record.date)
            except ValueError:
                return None
            if query.date_from and when < query.date_from:
                return None
            if query.date_to and when > query.date_to:
                return None

        matched = list(record.urls) if not query.url else []
        for value in query.url:
            found = [u for u in record.urls if value in u.lower()]
            if not found:
                return None
            matched = found

        if query.words and record.urls:
            word_matched = [
                u for u in record.urls if any(w in u.lower() for w in query.words)
            ]
            if word_matched:
                matched = word_matched
        return matched[:40]


def normalise_case(text: str) -> str:
    """Case numbers with punctuation and spacing removed: cscomm331of2025."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


_CASE = re.compile(
    r"\b((?:CS|C\.S\.|COMIPS|CO|W\.?P|S\.?B\.?|OMP|CM|FAO|RFA|TM|Writ|Suit|OS)"
    r"[^:]{0,70}?(?:No\.?\s*)?\d+\s*(?:/|of)\s*\d{2,4})",
    re.I,
)
_COURT = re.compile(
    r"(?:in|before|at)\s+the\s+(?:Hon[’'`]?ble\s+)*"
    r"((?:[A-Z][\w’'.\-]{1,20}\s+){0,3}"
    r"(?:High\s+Court|Supreme\s+Court|District\s+Court|Commercial\s+Court|Court)"
    r"[^.,;:]{0,60})",
    re.I,
)


def _case_from_title(title: str) -> str:
    m = _CASE.search(title or "")
    return m.group(1).strip() if m else ""


def _court_from_title(title: str) -> str:
    m = _COURT.search(title or "")
    return " ".join((m.group(1) if m else "").split()).rstrip(" .")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:180]
