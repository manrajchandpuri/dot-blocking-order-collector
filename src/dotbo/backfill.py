"""Build the back-catalogue, one month at a time.

Roughly thirty months and 475 orders sit behind this, which is a couple of hours
of polite downloading and about 1.8 GB of PDFs.  So it never starts on its own:
it is an explicit action, it skips months already in the library, and it
checkpoints after each month so stopping and resuming costs nothing.
"""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import library
from .catalog import Catalog
from .config import RunOptions
from .pipeline import run

# The earliest month DoT has published under these pages.
EARLIEST = (2023, 12)


def months_between(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Every (year, month) from start to end inclusive, oldest first."""
    year, month = start
    out: list[tuple[int, int]] = []
    while (year, month) <= end:
        out.append((year, month))
        year, month = (year + (month == 12), (month % 12) + 1)
    return out


def pending(start: tuple[int, int] | None = None, end: tuple[int, int] | None = None):
    """Months in range that are not in the library yet."""
    today = dt.date.today()
    start = start or EARLIEST
    end = end or (today.year, today.month)
    return [(y, m) for y, m in months_between(start, end) if not library.has(y, m)]


@dataclass
class Backfill:
    """A resumable run over many months."""

    start: tuple[int, int] = EARLIEST
    end: tuple[int, int] | None = None
    output_dir: Path | None = None
    use_ocr: bool = True
    tesseract_path: str = ""
    look_ahead_days: int = 31

    months: list[tuple[int, int]] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    current: str = ""
    finished: bool = False
    _stop: threading.Event = field(default_factory=threading.Event)

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def total(self) -> int:
        return len(self.months)

    def execute(self, on_progress=None, on_month=None) -> "Backfill":
        say = on_progress or (lambda *_: None)
        self.months = pending(self.start, self.end)
        say(f"{len(self.months)} months to build")
        # Warm the catalogue once so each month reuses it rather than re-reading
        # every listing page.
        Catalog().build_index(progress=lambda m: say(m))

        for year, month in self.months:
            if self._stop.is_set():
                say("Stopped")
                break
            slug = library.slug_for(year, month)
            self.current = library.label_for(year, month)
            say(f"Building {self.current}")
            options = RunOptions(
                year=year, month=month,
                look_ahead_days=self.look_ahead_days,
                use_ocr=self.use_ocr, tesseract_path=self.tesseract_path,
                output_dir=self.output_dir or (library.LIBRARY / slug),
                # Thirty months of ZIPs would be ~1.8 GB of PDFs already sitting
                # in the cache.  The library stores manifests; ZIPs are rebuilt.
                write_zip=bool(self.output_dir),
            )
            try:
                result = run(options)
                if result.orders:
                    self.done.append(slug)
                    say(f"{self.current}: {len(result.orders)} orders, {result.total_urls} URLs")
                else:
                    say(f"{self.current}: no orders published")
            except Exception as exc:  # noqa: BLE001 - one bad month must not end the job
                self.failed.append((slug, str(exc)))
                say(f"{self.current} failed: {exc}")
            if on_month:
                on_month(self)
        self.current = ""
        self.finished = True
        return self
