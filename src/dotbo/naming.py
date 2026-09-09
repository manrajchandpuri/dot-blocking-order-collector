"""Ordering and file naming, reproducing the reference compilations exactly.

Two rules, both derived from the August 2026 reference folder:

* **Order date** comes from the PDF's own covering letter, not from the DoT
  catalogue.  Attachment 84329 is published as 20/08/2026 and titled
  "instructions dated 20.08.2026", but the PDF says 19 August - and the human
  compilation calls it "dated 19 August 2026".
* **Sequence** is the catalogue in oldest-first order, stable-sorted by that
  date.  Matching every August file by byte size reproduces the reference
  numbering 1-22 exactly.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .catalog import Attachment
from .config import MONTHS
from .extract import Extraction
from .letter import Letter


@dataclass
class Order:
    """One blocking order, from catalogue row through to output filename."""

    attachment: Attachment
    pdf_path: str = ""
    letter: Letter | None = None
    extraction: Extraction | None = None
    index: int = 0                      # 1-based position in the compilation
    error: str = ""

    @property
    def order_date(self) -> dt.date | None:
        """The date the compilation is built around, best source first."""
        if self.letter and self.letter.order_date:
            return self.letter.order_date
        return self.attachment.title_date or self.attachment.published

    @property
    def date_source(self) -> str:
        if self.letter and self.letter.order_date:
            return "pdf"
        if self.attachment.title_date:
            return "title"
        return "published"

    @property
    def stem(self) -> str:
        return order_stem(self.index, self.order_date)

    @property
    def filename(self) -> str:
        return self.stem + ".pdf"

    @property
    def urls(self) -> list[str]:
        return list(self.extraction.urls) if self.extraction else []


def order_stem(index: int, date: dt.date | None) -> str:
    """``1_Blocking order dated 05 August 2026`` - the May-2025 convention."""
    if date is None:
        return f"{index}_Blocking order (date unknown)"
    return f"{index}_Blocking order dated {date.day:02d} {MONTHS[date.month - 1]} {date.year}"


def compilation_name(year: int, month: int) -> str:
    return f"Blocking Orders_{MONTHS[month - 1]} {year}"


def in_scope(date: dt.date | None, year: int, month: int, days: tuple[int, ...]) -> bool:
    if date is None:
        return False
    if (date.year, date.month) != (year, month):
        return False
    return not days or date.day in days


def sequence(orders: list[Order]) -> list[Order]:
    """Sort into reference order and assign 1..N.

    ``sorted`` is stable, so orders sharing a date keep their catalogue
    position - which is what the August reference folder does.
    """
    ordered = sorted(
        orders,
        key=lambda o: (o.order_date or dt.date.max, o.attachment.listing_id, o.attachment.seq),
    )
    for i, order in enumerate(ordered, start=1):
        order.index = i
    return ordered


_SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    return _SAFE.sub("-", name).strip() or "untitled"
