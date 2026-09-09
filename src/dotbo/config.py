"""Shared paths and tunables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = Path(os.environ.get("DOTBO_WORKSPACE", PROJECT_ROOT / "workspace"))
PDF_CACHE = WORKSPACE / "pdfs"
INDEX_CACHE = WORKSPACE / "index.json"
DATA_DIR = Path(__file__).resolve().parent / "data"

API_BASE = "https://www.dot.gov.in/cms/wp-json"
SEARCH_URL = API_BASE + "/wp/v2/search"
POST_URL = API_BASE + "/post-page/post"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


@dataclass
class RunOptions:
    """Everything the UI can tune for a single run."""

    year: int
    month: int
    days: tuple[int, ...] = ()          # empty => whole month
    look_ahead_days: int = 31           # publication lag window
    use_ocr: bool = True
    tesseract_path: str = ""
    refresh_index: bool = False
    redownload: bool = False
    max_workers: int = 8
    output_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    # The back-catalogue keeps manifests, not archives: a month's ZIP is a copy
    # of PDFs already in the cache, ~78 MB each, and is rebuilt on demand.
    write_zip: bool = True
    strict_collection: bool = False   # unattended runs must not hide source failures

    @property
    def month_name(self) -> str:
        return MONTHS[self.month - 1]

    @property
    def label(self) -> str:
        return f"{self.month_name} {self.year}"


def ensure_dirs() -> None:
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
