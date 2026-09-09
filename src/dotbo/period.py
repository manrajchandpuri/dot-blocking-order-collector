"""Understand the period a DoT listing page covers, from its title.

DoT groups blocking orders onto listing pages titled by period, and the naming
is not consistent.  The sibling "Termination Letters" pages in the same category
give the real range of what turns up::

    (July - Sept 2026)     (June-Sep 2026)      (Oct to Dec 2023)
    (Jan-May 2026)         (Jan to June 2024)   (May 2025)
    (June 2025-II)         (older than 2025)    (older than 2025 - 2)

So: hyphen, en-dash and the word "to"; abbreviated and full month names, with
``Sept`` as well as ``Sep``; single months; spans of any length; and roman
numeral part suffixes.

A parsed period is only ever a **hint**.  "older than 2025" actually holds
January to July 2025, so nothing here may be treated as authoritative - see
:mod:`dotbo.catalog`, which ranks pages by hint and then verifies against the
dates it actually finds.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass

RANGE = "range"        # a span of months, e.g. Jul-Sep 2026
SINGLE = "single"      # one month, e.g. May 2025
YEAR = "year"          # a whole year
OPEN = "open"          # "older than 2025" - open at the start
UNKNOWN = "unknown"    # unparseable; always treated as a possible match

MONTHS: dict[str, int] = {}
for _i, _name in enumerate(calendar.month_name[1:], start=1):
    MONTHS[_name.lower()] = _i          # january ... december
    MONTHS[_name[:3].lower()] = _i      # jan ... dec
MONTHS.update({"sept": 9, "june": 6, "july": 7})

_SEPARATOR = r"(?:\s*(?:-|‐|‑|‒|–|—|to|through|until|till|&|and)\s*)"
_MONTH = r"(?P<%s>" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")"
_YEAR = r"(?P<%s>19\d{2}|20\d{2})"

# "Jul - Sept 2026", "June-Sep 2026", "Oct to Dec 2023", "Jan-May 2026"
_RANGE_ONE_YEAR = re.compile(
    (_MONTH % "m1") + _SEPARATOR + (_MONTH % "m2") + r"\s*,?\s*" + (_YEAR % "y"),
    re.I,
)
# "Nov 2026 - Jan 2027"
_RANGE_TWO_YEARS = re.compile(
    (_MONTH % "m1") + r"\s*,?\s*" + (_YEAR % "y1") + _SEPARATOR
    + (_MONTH % "m2") + r"\s*,?\s*" + (_YEAR % "y2"),
    re.I,
)
# "May 2025", "June 2025-II"
_SINGLE = re.compile((_MONTH % "m") + r"\s*,?\s*" + (_YEAR % "y"), re.I)
# "older than 2025", "prior to 2025", "before 2025"
_OPEN = re.compile(r"(?:older\s+than|prior\s+to|before|upto|up\s+to)\s*" + (_YEAR % "y"), re.I)
_BARE_YEAR = re.compile(r"(?<!\d)" + (_YEAR % "y") + r"(?!\d)")


@dataclass(frozen=True)
class Period:
    """The months a listing page claims to cover."""

    start: dt.date | None      # first day of the first month
    end: dt.date | None        # last day of the last month
    kind: str = UNKNOWN
    text: str = ""             # the fragment we parsed

    @property
    def certain(self) -> bool:
        """Is this a definite span, rather than a guess or a catch-all?"""
        return self.kind in (RANGE, SINGLE, YEAR)

    def covers(self, year: int, month: int) -> bool:
        """Could this period contain the given month?

        ``open`` and ``unknown`` periods say yes to everything - being wrong in
        that direction only costs an extra page fetch, while being wrong the
        other way loses orders.
        """
        if self.kind in (OPEN, UNKNOWN) or self.start is None or self.end is None:
            if self.kind == OPEN and self.end is not None:
                return dt.date(year, month, 1) <= self.end
            return True
        first = dt.date(year, month, 1)
        return self.start <= first <= self.end

    def overlaps(self, start: dt.date, end: dt.date) -> bool:
        if self.kind in (OPEN, UNKNOWN) or self.start is None or self.end is None:
            if self.kind == OPEN and self.end is not None:
                return start <= self.end
            return True
        return self.start <= end and start <= self.end

    def __str__(self) -> str:
        if self.kind == UNKNOWN:
            return "unknown period"
        if self.kind == OPEN and self.end:
            return f"up to {self.end:%b %Y}"
        if self.start and self.end:
            if self.kind == SINGLE:
                return f"{self.start:%b %Y}"
            if self.kind == YEAR:
                return f"{self.start:%Y}"
            return f"{self.start:%b %Y} - {self.end:%b %Y}"
        return self.kind


def _month_start(year: int, month: int) -> dt.date:
    return dt.date(year, month, 1)


def _month_end(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _bracketed(title: str) -> str:
    """The parenthesised tail DoT puts the period in, or the whole title."""
    matches = re.findall(r"\(([^()]*)\)", title or "")
    return matches[-1] if matches else (title or "")


def parse_period(title: str) -> Period:
    """Best-effort period for a listing page title."""
    text = _bracketed(title)

    m = _RANGE_TWO_YEARS.search(text)
    if m:
        y1, y2 = int(m.group("y1")), int(m.group("y2"))
        m1, m2 = MONTHS[m.group("m1").lower()], MONTHS[m.group("m2").lower()]
        return Period(_month_start(y1, m1), _month_end(y2, m2), RANGE, m.group(0))

    m = _RANGE_ONE_YEAR.search(text)
    if m:
        year = int(m.group("y"))
        m1, m2 = MONTHS[m.group("m1").lower()], MONTHS[m.group("m2").lower()]
        if m1 <= m2:
            return Period(_month_start(year, m1), _month_end(year, m2), RANGE, m.group(0))
        # "Nov - Feb 2027" reads as spilling over the new year.
        return Period(_month_start(year - 1, m1), _month_end(year, m2), RANGE, m.group(0))

    m = _OPEN.search(text)
    if m:
        year = int(m.group("y"))
        return Period(None, _month_end(year - 1, 12), OPEN, m.group(0))

    m = _SINGLE.search(text)
    if m:
        year, month = int(m.group("y")), MONTHS[m.group("m").lower()]
        return Period(_month_start(year, month), _month_end(year, month), SINGLE, m.group(0))

    years = _BARE_YEAR.findall(text)
    if len(years) == 1:
        year = int(years[0])
        return Period(_month_start(year, 1), _month_end(year, 12), YEAR, years[0])
    if len(years) > 1:
        lo, hi = min(int(y) for y in years), max(int(y) for y in years)
        return Period(_month_start(lo, 1), _month_end(hi, 12), RANGE, " ".join(years))

    return Period(None, None, UNKNOWN, text)
