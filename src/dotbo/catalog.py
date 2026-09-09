"""Discover DoT blocking orders and work out which listing page holds a month.

The public site is a Next.js shell; the data comes from these endpoints::

  wp/v2/search?search=blocking&subtype=documents  -> the period listing pages
  wp/v2/documents?documents_category=987          -> the same pages, by category
  post-page/post?id=<listing page id>             -> acf_data.file[] = attachment ids
  post-page/post?id=<attachment id>               -> file_date, title, pdf.url, filesize

Two things matter here beyond plumbing.

**Discovery has to survive DoT renaming things.**  Pages are found by search
*and* by category, so a future "Oct - Dec 2026" page turns up either way, and a
retitled page still turns up through the category.

**A month has to resolve to the right page or two, not to all of them.**  Each
page's title gives a period hint (:mod:`dotbo.period`), pages are ranked against
the month being built, and only the plausible ones are fetched.  Crucially the
hint is *only* a hint - "older than 2025" actually holds January to July 2025 -
so what a page really contains is recorded as it is read, and the ranking falls
back to fetching everything rather than ever silently missing orders.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
import json
import re
import threading
from dataclasses import asdict, dataclass, field

from .config import API_BASE, INDEX_CACHE, POST_URL, SEARCH_URL, WORKSPACE
from .http import PoliteSession, shared
from .links import page_url, slug_from_cms_url
from .period import Period, parse_period

DOCUMENTS_URL = API_BASE + "/wp/v2/documents"

# Short enough to survive a retitle, specific enough not to drag in noise.
SEARCH_TERM = "blocking"
# "Data Services" under "Orders and Notices" - where these pages live.
DOCUMENTS_CATEGORY = 987
TITLE_MARKER = "blocking notification"

CACHE_VERSION = 2

_TITLE_DATE = re.compile(r"dated\s+(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", re.I)


# ---------------------------------------------------------------- records


@dataclass
class Attachment:
    """One blocking order as the DoT catalogue knows it."""

    att_id: int
    listing_id: int
    seq: int                      # position in oldest-first order within its page
    title: str = ""
    file_date: str = ""           # DD/MM/YYYY, the *publication* date
    pdf_url: str = ""
    filesize: int = 0
    post_name: str = ""           # WordPress's own slug, when we have fetched it

    @property
    def published(self) -> dt.date | None:
        return _parse_ddmmyyyy(self.file_date)

    @property
    def title_date(self) -> dt.date | None:
        m = _TITLE_DATE.search(self.title or "")
        if not m:
            return None
        try:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None

    @property
    def public_url(self) -> str:
        return page_url(self.att_id, self.post_name or _slugify(self.title))


@dataclass
class ListingPage:
    """A period page, with what its title claims and what it actually held."""

    page_id: int
    title: str
    slug: str = ""
    period: Period = field(default_factory=lambda: parse_period(""))
    observed_first: str = ""      # ISO date, from file_date values actually seen
    observed_last: str = ""
    attachment_ids: list[int] = field(default_factory=list)

    @property
    def public_url(self) -> str:
        return page_url(self.page_id, self.slug, self.title)

    @property
    def observed_span(self) -> tuple[dt.date, dt.date] | None:
        try:
            return (
                dt.date.fromisoformat(self.observed_first),
                dt.date.fromisoformat(self.observed_last),
            )
        except ValueError:
            return None

    def could_hold(self, start: dt.date, end: dt.date) -> bool:
        """Could this page hold orders published in [start, end)?

        Observation beats the title wherever we have it.
        """
        span = self.observed_span
        if span is not None:
            return span[0] <= end and start <= span[1]
        return self.period.overlaps(start, end)


# ---------------------------------------------------------------- catalogue


class Catalog:
    """Finds listing pages, and the orders on the ones that matter."""

    def __init__(self, session: PoliteSession | None = None, max_workers: int = 4, strict: bool = False):
        # The site blocks the client IP after a burst, so keep concurrency low
        # and let PoliteSession space the requests out.
        self.session = session or shared()
        self.max_workers = max_workers
        self.strict = strict
        self._lock = threading.Lock()
        self._cache = _load_cache()
        self._pages: list[ListingPage] | None = None

    # ---------------------------------------------------------------- network

    def _get_json(self, url: str, params: dict | None = None):
        return self.session.json(url, params)

    def listing_pages(self, refresh: bool = False) -> list[ListingPage]:
        """Every blocking-notification listing page, newest period first.

        Found two ways so that neither a retitle nor a search-ranking change can
        hide a page from us.
        """
        if self._pages is not None and not refresh:
            return self._pages

        found: dict[int, str] = {}
        for finder in (self._pages_by_search, self._pages_by_category):
            try:
                for page_id, title in finder():
                    found.setdefault(page_id, title)
            except Exception:  # noqa: BLE001 - one path failing must not blind us
                if self.strict:
                    raise
                continue

        cached_pages = self._cache.get("pages", {})
        pages: list[ListingPage] = []
        for page_id, title in found.items():
            row = cached_pages.get(str(page_id), {})
            pages.append(
                ListingPage(
                    page_id=page_id,
                    title=title,
                    slug=row.get("slug") or _slugify(title),
                    period=parse_period(title),
                    observed_first=row.get("observed_first", ""),
                    observed_last=row.get("observed_last", ""),
                    attachment_ids=list(row.get("attachment_ids", [])),
                )
            )
        # Newest period first; unknown periods last.
        pages.sort(key=lambda p: (p.period.end or dt.date.min), reverse=True)
        if self.strict and not pages:
            raise RuntimeError("No DoT listing pages found; source discovery needs investigation")
        self._pages = pages
        return pages

    def _pages_by_search(self):
        data = self._get_json(
            SEARCH_URL, {"search": SEARCH_TERM, "subtype": "documents", "per_page": 100}
        )
        for row in data or []:
            if not isinstance(row, dict) or "id" not in row:
                continue
            title = _unescape(row.get("title", ""))
            if TITLE_MARKER in title.lower():
                slug = slug_from_cms_url(row.get("url", ""))
                if slug:
                    self._remember_slug(int(row["id"]), slug, title)
                yield int(row["id"]), title

    def _pages_by_category(self):
        data = self._get_json(
            DOCUMENTS_URL,
            {"documents_category": DOCUMENTS_CATEGORY, "per_page": 100, "_fields": "id,title,slug"},
        )
        for row in data or []:
            if not isinstance(row, dict) or "id" not in row:
                continue
            raw = row.get("title")
            title = _unescape(raw.get("rendered") if isinstance(raw, dict) else raw)
            if TITLE_MARKER in title.lower():
                if row.get("slug"):
                    self._remember_slug(int(row["id"]), str(row["slug"]), title)
                yield int(row["id"]), title

    def _remember_slug(self, page_id: int, slug: str, title: str = "") -> None:
        pages = self._cache.setdefault("pages", {})
        row = pages.setdefault(str(page_id), {})
        row["slug"] = slug
        if title:
            row["title"] = title

    def attachment_ids(self, listing_id: int) -> list[int]:
        """Attachment ids on a listing page, oldest first, duplicates removed."""
        data = self._get_json(POST_URL, {"id": listing_id})
        files = (data.get("posts", {}).get("acf_data", {}) or {}).get("file") or []
        ids: list[int] = []
        for entry in files:
            ref = entry.get("file") if isinstance(entry, dict) else None
            if isinstance(ref, list) and ref:
                ids.append(int(ref[0]))
            elif isinstance(ref, int):
                ids.append(ref)
        ids.reverse()  # site lists newest first; we want oldest first
        if self.strict and not ids:
            raise RuntimeError(f"Listing {listing_id} has no readable attachment IDs")
        out, seen = [], set()
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    def attachment(self, att_id: int, listing_id: int, seq: int) -> Attachment:
        data = self._get_json(POST_URL, {"id": att_id})
        post = data.get("posts", {}) or {}
        acf = post.get("acf_data", {}) or {}
        pdf = acf.get("pdf") or {}
        if isinstance(pdf, list):
            pdf = pdf[0] if pdf else {}
        attachment = Attachment(
            att_id=att_id,
            listing_id=listing_id,
            seq=seq,
            title=post.get("post_title", "") or acf.get("title", "") or "",
            post_name=post.get("post_name", "") or "",
            file_date=acf.get("file_date", "") or "",
            pdf_url=(pdf or {}).get("url", "") or "",
            filesize=int((pdf or {}).get("filesize") or 0),
        )
        if self.strict and (not attachment.pdf_url or not (attachment.published or attachment.title_date)):
            raise RuntimeError(f"Attachment {att_id} is missing a PDF URL or usable date metadata")
        return attachment

    # ---------------------------------------------------------------- routing

    def rank_pages(
        self, year: int, month: int, look_ahead_days: int = 31, refresh: bool = False
    ) -> list[tuple[int, ListingPage]]:
        """Listing pages scored for how likely they are to hold this month.

        3 - the title's period covers the month itself
        2 - it covers the look-ahead window, i.e. the month after (an order
            dated 30 September is published in October and filed on that page)
        1 - open-ended, unparseable, observed to touch the window, or simply the
            newest page there is
        0 - the title excludes it and observation does not contradict that
        """
        start, end = month_window(year, month, look_ahead_days)
        nxt_year, nxt_month = _next_month(year, month)
        pages = self.listing_pages(refresh=refresh)
        newest = self._newest_page(pages)
        scored: list[tuple[int, ListingPage]] = []
        for page in pages:
            span = page.observed_span
            if page.period.covers(year, month) and page.period.certain:
                score = 3
            elif page.period.covers(nxt_year, nxt_month) and page.period.certain:
                score = 2
            elif not page.period.certain:
                score = 1
            elif span is not None and span[0] <= end and start <= span[1]:
                score = 1          # the title says no, but we have seen otherwise
            elif page is newest and end >= (page.period.end or dt.date.min):
                # Until DoT creates the next period page, new orders are appended
                # to the newest one - so a month past the end of every known
                # period is worth looking for there.
                score = 1
            else:
                score = 0
            scored.append((score, page))
        scored.sort(key=lambda pair: (-pair[0], -(pair[1].period.end or dt.date.min).toordinal()))
        return scored

    @staticmethod
    def _newest_page(pages: list[ListingPage]) -> ListingPage | None:
        dated = [p for p in pages if p.period.end or p.observed_span]
        if not dated:
            return pages[0] if pages else None
        return max(
            dated,
            key=lambda p: (p.observed_span[1] if p.observed_span else p.period.end) or dt.date.min,
        )

    def page_attachments(
        self, page: ListingPage, refresh: bool = False, progress=None
    ) -> list[Attachment]:
        """Every order on one listing page, fetching only what is not cached."""
        ids = self.attachment_ids(page.page_id)
        page.attachment_ids = ids
        cached = self._cache.setdefault("attachments", {})
        missing = [i for i in ids if str(i) not in cached] if not refresh else list(ids)
        if missing and progress:
            progress(f"Reading {len(missing)} new orders from {page.period}")

        if missing:
            done = 0
            with concurrent.futures.ThreadPoolExecutor(self.max_workers) as pool:
                futures = {
                    pool.submit(self.attachment, att_id, page.page_id, ids.index(att_id)): att_id
                    for att_id in missing
                }
                for future in concurrent.futures.as_completed(futures):
                    try:
                        att = future.result()
                    except Exception:  # noqa: BLE001 - a dead id must not kill the run
                        if self.strict:
                            raise
                        continue
                    with self._lock:
                        cached[str(att.att_id)] = asdict(att)
                    done += 1
                    if progress and done % 20 == 0:
                        progress(f"Read {done} of {len(missing)} orders")

        out: list[Attachment] = []
        for seq, att_id in enumerate(ids):
            row = cached.get(str(att_id))
            if not row:
                continue
            row = dict(row)
            row["listing_id"] = page.page_id
            row["seq"] = seq
            out.append(Attachment(**row))

        dates = sorted(a.published for a in out if a.published)
        if dates:
            page.observed_first = dates[0].isoformat()
            page.observed_last = dates[-1].isoformat()
        self._store_page(page)
        _save_cache(self._cache)
        return out

    def candidates_for_month(
        self,
        year: int,
        month: int,
        look_ahead_days: int = 31,
        refresh: bool = False,
        progress=None,
    ) -> list[Attachment]:
        """Orders that could belong to this month, from the pages that can hold them.

        Publication never precedes the order date, so the window runs from the
        first of the month to the month end plus ``look_ahead_days``.
        """
        say = progress or (lambda *_: None)
        start, end = month_window(year, month, look_ahead_days)
        ranked = self.rank_pages(year, month, look_ahead_days, refresh=refresh)
        likely = [(s, p) for s, p in ranked if s >= 2]
        possible = [(s, p) for s, p in ranked if s == 1]

        if likely:
            say(
                f"{_month_name(month)} {year} is on: "
                + "; ".join(str(p.period) for _s, p in likely)
            )
        collected: list[Attachment] = []
        read: set[int] = set()

        def read_page(page: ListingPage) -> None:
            if page.page_id in read:
                return
            read.add(page.page_id)
            collected.extend(self.page_attachments(page, refresh=refresh, progress=say))

        for _score, page in likely:
            read_page(page)

        # Widen to the uncertain pages unless what they were observed to hold
        # already rules them out.  A month can also straddle two pages, so this
        # runs even when the likely pages produced results.
        for page in (p for _s, p in possible if p.could_hold(start, end)):
            say(f"Also checking {page.period}")
            read_page(page)

        found = _in_window(collected, year, month, start, end)
        if not found and len(read) < len(ranked):
            # The period titles led us nowhere.  Correctness must not depend on
            # DoT's naming, so fall back to reading every listing page.
            say("Nothing on the expected pages; checking every listing page")
            for _s, page in ranked:
                read_page(page)
            found = _in_window(collected, year, month, start, end)
        return found

    # ---------------------------------------------------------------- index

    def build_index(self, refresh: bool = False, progress=None) -> list[Attachment]:
        """Every order on every listing page - for search and the back-catalogue."""
        say = progress or (lambda *_: None)
        pages = self.listing_pages(refresh=refresh)
        say(f"Found {len(pages)} DoT listing pages")
        out: list[Attachment] = []
        for page in pages:
            out.extend(self.page_attachments(page, refresh=refresh, progress=say))
        return out

    def cached_pages(self) -> list[ListingPage]:
        """Listing pages from the on-disk cache, with no network access."""
        out: list[ListingPage] = []
        for page_id, row in (self._cache.get("pages") or {}).items():
            title = row.get("title", "")
            out.append(
                ListingPage(
                    page_id=int(page_id),
                    title=title,
                    slug=row.get("slug") or _slugify(title),
                    period=parse_period(title),
                    observed_first=row.get("observed_first", ""),
                    observed_last=row.get("observed_last", ""),
                    attachment_ids=list(row.get("attachment_ids", [])),
                )
            )
        return out

    def cached_attachments(self) -> list[Attachment]:
        """Whatever is already on disk, with no network access at all."""
        rows = self._cache.get("attachments", {})
        return [Attachment(**row) for row in rows.values()]

    def _store_page(self, page: ListingPage) -> None:
        pages = self._cache.setdefault("pages", {})
        pages[str(page.page_id)] = {
            "title": page.title,
            "slug": page.slug,
            "observed_first": page.observed_first,
            "observed_last": page.observed_last,
            "attachment_ids": page.attachment_ids,
        }


# ---------------------------------------------------------------- helpers


def _in_window(
    attachments: list[Attachment], year: int, month: int, start: dt.date, end: dt.date
) -> list[Attachment]:
    """Orders published inside the window, or titled with the target month."""
    seen: set[int] = set()
    out: list[Attachment] = []
    for att in attachments:
        if att.att_id in seen:
            continue
        seen.add(att.att_id)
        pub, tdate = att.published, att.title_date
        if (pub and start <= pub < end) or (
            tdate and (tdate.year, tdate.month) == (year, month)
        ):
            out.append(att)
    return out


def month_window(year: int, month: int, look_ahead_days: int = 31) -> tuple[dt.date, dt.date]:
    start = dt.date(year, month, 1)
    nxt_year, nxt_month = _next_month(year, month)
    return start, dt.date(nxt_year, nxt_month, 1) + dt.timedelta(days=look_ahead_days)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + (month == 12), (month % 12) + 1)


def _month_name(month: int) -> str:
    import calendar

    return calendar.month_name[month]


def _parse_ddmmyyyy(value: str) -> dt.date | None:
    if not value:
        return None
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", value)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _unescape(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _slugify(text: str) -> str:
    """WordPress's slug rule, as observed on this site.

    Apostrophes are *removed* rather than replaced, so "Hon'ble" becomes
    "honble" and not "hon-ble" - which is the whole difference between a working
    dot.gov.in link and a broken one.
    """
    cleaned = re.sub(r"['‘’ʼ`]", "", (text or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")[:180]


def _load_cache() -> dict:
    try:
        with INDEX_CACHE.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001 - a bad cache is just a cold cache
        return {"version": CACHE_VERSION, "attachments": {}, "pages": {}}
    if data.get("version") == CACHE_VERSION:
        data.setdefault("attachments", {})
        data.setdefault("pages", {})
        return data
    # Version 1 was a flat {att_id: record} map.  Keep every record.
    if data and all(isinstance(v, dict) and "att_id" in v for v in data.values()):
        return {"version": CACHE_VERSION, "attachments": data, "pages": {}}
    return {"version": CACHE_VERSION, "attachments": {}, "pages": {}}


_SAVE_LOCK = threading.Lock()


def _save_cache(data: dict) -> None:
    with _SAVE_LOCK:
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        tmp = INDEX_CACHE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        tmp.replace(INDEX_CACHE)
