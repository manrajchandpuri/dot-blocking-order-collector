"""End-to-end run: catalogue -> PDFs -> URLs -> ZIP + Word + QA report."""
from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import docxout, fetch, library, report
from .catalog import Attachment, Catalog
from .config import RunOptions
from .extract import extract
from .letter import parse_letter
from .naming import Order, compilation_name, in_scope, sequence
from .pdftext import Document
from .progress import CATALOGUE, DONE, DOWNLOAD, EXTRACT, READ, WRITE, Reporter


@dataclass
class RunResult:
    options: RunOptions
    orders: list[Order] = field(default_factory=list)
    zip_path: Path | None = None
    docx_path: Path | None = None
    report_csv: Path | None = None
    report_html: Path | None = None
    skipped: list[tuple[Attachment, str]] = field(default_factory=list)
    stored: object | None = None        # library.StoredMonth, once recorded

    @property
    def total_urls(self) -> int:
        return sum(len(o.urls) for o in self.orders)

    @property
    def uncorroborated(self) -> list[Order]:
        """Orders the engine could find no independent support for."""
        return [o for o in self.orders if report.uncorroborated(o)]

    @property
    def corroborated_count(self) -> int:
        return len(self.orders) - len(self.uncorroborated)

    @property
    def files(self) -> list[Path]:
        return [
            p
            for p in (self.zip_path, self.docx_path, self.report_html, self.report_csv)
            if p and Path(p).is_file()
        ]


def discover(options: RunOptions, on_progress=None) -> tuple[list[Order], list[tuple[Attachment, str]]]:
    """Find and read the orders for the requested month, in reference order.

    The date the compilation is built around lives inside the PDF, so every
    plausible candidate has to be downloaded before it can be filtered.
    """
    say = Reporter(on_progress)

    say(CATALOGUE, "Looking up the DoT catalogue", 0, 3)
    catalog = Catalog(max_workers=options.max_workers, strict=options.strict_collection)
    candidates = catalog.candidates_for_month(
        options.year,
        options.month,
        look_ahead_days=options.look_ahead_days,
        refresh=options.refresh_index,
        progress=lambda m: say(CATALOGUE, m, 1, 3),
    )
    say(
        CATALOGUE,
        f"{len(candidates)} orders published near {options.label}",
        3,
        3,
    )

    total = len(candidates)
    say(DOWNLOAD, f"Downloading {total} order PDFs", 0, total)
    downloads = fetch.download_all(
        candidates,
        force=options.redownload,
        max_workers=min(options.max_workers, 4),
        progress=lambda done, n, att: say(
            DOWNLOAD, f"Downloaded {done} of {n} PDFs", done, n
        ),
    )

    orders: list[Order] = []
    skipped: list[tuple[Attachment, str]] = []
    for i, att in enumerate(candidates, start=1):
        say(READ, f"Reading covering letter {i} of {total}", i, total)
        result = downloads.get(att.att_id)
        if isinstance(result, Exception) or result is None:
            skipped.append((att, f"download failed: {result}"))
            continue
        try:
            letter = _read_letter(result, options)
        except Exception as exc:  # noqa: BLE001 - a broken PDF is reported, not fatal
            skipped.append((att, f"could not read PDF: {exc}"))
            continue
        order = Order(attachment=att, pdf_path=str(result), letter=letter)
        if options.strict_collection and not letter.order_date:
            skipped.append((att, "covering-letter date could not be established"))
            continue
        if in_scope(order.order_date, options.year, options.month, options.days):
            orders.append(order)

    ordered = sequence(orders)
    say(READ, f"{len(ordered)} orders dated in {options.label}", total, total)
    return ordered, skipped


def _read_letter(path, options: RunOptions):
    """Parse the covering letter, falling back to OCR when page 1 is scanned.

    Older orders are published as scans with no text layer at all, so reading
    page 1 without OCR yields nothing - no date, no declared count, no
    block/unblock.  OCR is slow, so it is only paid for on the documents that
    actually need it.
    """
    with Document(path, use_ocr=False) as doc:
        letter = parse_letter(doc.page(1).text)
    if letter.order_date or not options.use_ocr:
        return letter
    with Document(path, use_ocr=True, tesseract_path=options.tesseract_path) as doc:
        return parse_letter(doc.page(1).text)


def run(options: RunOptions, on_progress=None, on_order=None) -> RunResult:
    say = Reporter(on_progress)
    orders, skipped = discover(options, on_progress=on_progress)
    result = RunResult(options=options, orders=orders, skipped=skipped)

    total = len(orders)
    say(EXTRACT, f"Extracting URLs from {total} orders", 0, total)
    for order in orders:
        try:
            with Document(
                order.pdf_path,
                use_ocr=options.use_ocr,
                tesseract_path=options.tesseract_path,
            ) as doc:
                letter, extraction = extract(doc, order.letter)
            order.letter, order.extraction = letter, extraction
        except Exception as exc:  # noqa: BLE001 - one bad order must not stop the run
            order.error = str(exc)
        say(
            EXTRACT,
            f"{order.stem} - {len(order.urls)} URLs",
            order.index,
            total,
        )
        if on_order:
            on_order(order)

    out_dir = Path(options.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = compilation_name(options.year, options.month)
    if options.days:
        name += " (" + ", ".join(str(d) for d in sorted(options.days)) + ")"

    if options.write_zip:
        say(WRITE, "Writing the ZIP of renamed PDFs", 1, 4)
        result.zip_path = _write_zip(orders, out_dir / f"{name}.zip")
    say(WRITE, "Writing the consolidated Word document", 2, 4)
    result.docx_path = docxout.build(orders, options.year, options.month, out_dir / f"{name}.docx")
    say(WRITE, "Writing the QA report", 3, 4)
    result.report_csv = report.write_csv(orders, out_dir / f"{name} - QA report.csv")
    result.report_html = report.write_html(
        orders, options.year, options.month, out_dir / f"{name} - QA report.html"
    )
    if orders:
        result.stored = library.save(result)
    say(
        DONE,
        f"{len(orders)} orders, {result.total_urls} URLs, "
        f"{result.corroborated_count}/{len(orders)} counts verified",
        1,
        1,
    )
    return result


def _write_zip(orders: list[Order], path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for order in orders:
            if order.pdf_path and Path(order.pdf_path).is_file():
                zf.write(order.pdf_path, arcname=order.filename)
    return path


def staged_copy(orders: list[Order], target: Path) -> Path:
    """Write the renamed PDFs into a plain folder as well as the ZIP."""
    target.mkdir(parents=True, exist_ok=True)
    for order in orders:
        if order.pdf_path and Path(order.pdf_path).is_file():
            shutil.copy2(order.pdf_path, target / order.filename)
    return target
