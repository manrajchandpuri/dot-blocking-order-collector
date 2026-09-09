"""Structured progress events, so a caller can draw a real progress bar.

A run has five stages of very different lengths, so each one owns a slice of
the overall bar and reports its own position inside that slice.  That keeps the
bar monotonic and roughly honest about how much is left.
"""
from __future__ import annotations

from dataclasses import dataclass

CATALOGUE = "catalogue"
DOWNLOAD = "download"
READ = "read"
EXTRACT = "extract"
WRITE = "write"
DONE = "done"

# stage -> (start, end) of the overall bar
SPANS: dict[str, tuple[float, float]] = {
    CATALOGUE: (0.00, 0.06),
    DOWNLOAD: (0.06, 0.34),
    READ: (0.34, 0.44),
    EXTRACT: (0.44, 0.92),
    WRITE: (0.92, 1.00),
    DONE: (1.00, 1.00),
}

LABELS: dict[str, str] = {
    CATALOGUE: "Checking the DoT catalogue",
    DOWNLOAD: "Downloading order PDFs",
    READ: "Reading covering letters",
    EXTRACT: "Extracting blocked URLs",
    WRITE: "Writing the ZIP, Word document and QA report",
    DONE: "Ready",
}

ORDER = (CATALOGUE, DOWNLOAD, READ, EXTRACT, WRITE, DONE)


@dataclass
class Progress:
    """Where the run has got to."""

    stage: str
    message: str = ""
    current: int = 0
    total: int = 0

    @property
    def label(self) -> str:
        return LABELS.get(self.stage, self.stage)

    @property
    def fraction(self) -> float:
        """Position on the overall 0-1 bar."""
        start, end = SPANS.get(self.stage, (0.0, 1.0))
        if self.total <= 0:
            return start
        within = min(max(self.current / self.total, 0.0), 1.0)
        return start + (end - start) * within

    @property
    def detail(self) -> str:
        if self.total > 0:
            return f"{self.label} - {self.current}/{self.total}"
        return self.label

    def __str__(self) -> str:  # so a plain print(...) callback still reads well
        return self.message or self.detail


class Reporter:
    """Adapts one callback into per-stage emitters."""

    def __init__(self, callback=None):
        self._callback = callback

    def __call__(self, stage: str, message: str = "", current: int = 0, total: int = 0) -> None:
        if self._callback is None:
            return
        self._callback(Progress(stage, message, current, total))
