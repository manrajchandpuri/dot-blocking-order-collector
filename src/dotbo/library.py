"""A durable record of every month that has been built.

Storing the finished ZIP would double the disk for nothing: it is a copy of
PDFs already sitting in ``workspace/pdfs/``, ~78 MB for a single month and about
1.8 GB across the full back-catalogue.  So a month keeps its manifest, its Word
document and its QA reports - a few hundred KB - and the ZIP is rebuilt on
demand, re-fetching any PDF that has since been evicted from the cache.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import MONTHS, WORKSPACE
from .links import page_url

LIBRARY = WORKSPACE / "library"
MANIFEST = "manifest.json"
SCHEMA = 1


@dataclass
class StoredOrder:
    """One order as the library remembers it."""

    index: int
    filename: str
    attachment_id: int
    order_date: str = ""
    case_no: str = ""
    court: str = ""
    action: str = "block"
    subject: str = ""
    urls: list[str] = field(default_factory=list)
    declared_count: int | None = None
    corroborated: bool = False
    corroboration: str = ""
    source: str = ""
    ocr_engine: str = ""
    pdf_url: str = ""
    filesize: int = 0

    @property
    def public_url(self) -> str:
        return page_url(self.attachment_id, _slug(self.subject or self.case_no or "order"))


@dataclass
class StoredMonth:
    """A built month: what it contains and where its files are."""

    year: int
    month: int
    slug: str
    label: str
    built_at: str
    order_count: int
    url_count: int
    corroborated_count: int
    days: list[int] = field(default_factory=list)
    orders: list[StoredOrder] = field(default_factory=list)

    @property
    def directory(self) -> Path:
        return LIBRARY / self.slug

    @property
    def docx_path(self) -> Path:
        return self.directory / f"{self.label}.docx"

    @property
    def report_html(self) -> Path:
        return self.directory / f"{self.label} - QA report.html"

    @property
    def report_csv(self) -> Path:
        return self.directory / f"{self.label} - QA report.csv"

    @property
    def built_on(self) -> str:
        try:
            return f"{dt.datetime.fromisoformat(self.built_at):%d %b %Y}"
        except ValueError:
            return self.built_at


def slug_for(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def label_for(year: int, month: int) -> str:
    return f"Blocking Orders_{MONTHS[month - 1]} {year}"


# ---------------------------------------------------------------- writing


def save(result) -> StoredMonth:
    """Record a completed run.  Overwrites any earlier build of that month."""
    options = result.options
    directory = LIBRARY / slug_for(options.year, options.month)
    directory.mkdir(parents=True, exist_ok=True)

    orders: list[StoredOrder] = []
    for order in result.orders:
        ex, letter = order.extraction, order.letter
        orders.append(
            StoredOrder(
                index=order.index,
                filename=order.filename,
                attachment_id=order.attachment.att_id,
                order_date=order.order_date.isoformat() if order.order_date else "",
                case_no=letter.case_no if letter else "",
                court=letter.court if letter else "",
                action=letter.action if letter else "block",
                subject=(letter.subject if letter else "")[:300],
                urls=list(order.urls),
                declared_count=ex.declared_count if ex else None,
                corroborated=bool(ex and ex.corroborated),
                corroboration=ex.corroboration if ex else "",
                source=ex.source if ex else "",
                ocr_engine=ex.ocr_engine if ex else "",
                pdf_url=order.attachment.pdf_url,
                filesize=order.attachment.filesize,
            )
        )

    stored = StoredMonth(
        year=options.year,
        month=options.month,
        slug=slug_for(options.year, options.month),
        label=label_for(options.year, options.month),
        built_at=dt.datetime.now().isoformat(timespec="seconds"),
        order_count=len(orders),
        url_count=sum(len(o.urls) for o in orders),
        corroborated_count=sum(1 for o in orders if o.corroborated),
        days=sorted(options.days),
        orders=orders,
    )

    for source, target in (
        (result.docx_path, stored.docx_path),
        (result.report_html, stored.report_html),
        (result.report_csv, stored.report_csv),
    ):
        if source and Path(source).is_file() and Path(source) != target:
            shutil.copy2(source, target)

    payload = asdict(stored)
    payload["schema"] = SCHEMA
    (directory / MANIFEST).write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return stored


# ---------------------------------------------------------------- reading


def load(slug: str) -> StoredMonth | None:
    path = LIBRARY / slug / MANIFEST
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a damaged manifest is a missing one
        return None
    payload.pop("schema", None)
    try:
        payload["orders"] = [StoredOrder(**row) for row in payload.get("orders", [])]
        return StoredMonth(**payload)
    except TypeError:
        return None


def entries() -> list[StoredMonth]:
    """Every stored month, newest first."""
    if not LIBRARY.is_dir():
        return []
    months = [m for m in (load(p.name) for p in LIBRARY.iterdir() if p.is_dir()) if m]
    months.sort(key=lambda m: (m.year, m.month), reverse=True)
    return months


def has(year: int, month: int) -> bool:
    return (LIBRARY / slug_for(year, month) / MANIFEST).is_file()


# ---------------------------------------------------------------- zip


def rebuild_zip(slug: str, dest: Path | None = None, progress=None) -> Path:
    """Regenerate a month's ZIP from the cached PDFs, fetching any that are gone."""
    from . import fetch
    from .catalog import Attachment

    month = load(slug)
    if month is None:
        raise FileNotFoundError(f"no stored month {slug!r}")
    say = progress or (lambda *_: None)

    missing = [o for o in month.orders if not (fetch.PDF_CACHE / f"{o.attachment_id}.pdf").is_file()]
    if missing:
        say(f"Re-fetching {len(missing)} PDFs that are no longer cached")
        fetch.download_all(
            [
                Attachment(
                    att_id=o.attachment_id, listing_id=0, seq=i,
                    pdf_url=o.pdf_url, filesize=o.filesize,
                )
                for i, o in enumerate(missing)
            ],
            max_workers=4,
        )

    dest = Path(dest) if dest else month.directory / f"{month.label}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for order in sorted(month.orders, key=lambda o: o.index):
            pdf = fetch.PDF_CACHE / f"{order.attachment_id}.pdf"
            if pdf.is_file():
                zf.write(pdf, arcname=order.filename)
    say(f"Built {dest.name}")
    return dest


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:180]
