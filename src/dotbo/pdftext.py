"""PyMuPDF page access with a transparent OCR fallback.

pypdf raises on some of these files (``15_Blocking Order dated 19 August
2026.pdf``), so PyMuPDF is the only text engine.  Pages that carry no extractable
text but do carry images are rendered and sent to :mod:`dotbo.ocr`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import ocr as ocr_mod

# These orders carry malformed annotations; MuPDF writes a warning per page to
# stderr that says nothing useful about the extraction.
try:
    pymupdf.TOOLS.mupdf_display_errors(False)
except Exception:  # noqa: BLE001
    pass

MIN_TEXT_CHARS = 40        # below this a page is definitely scanned
MIN_MEANINGFUL_CHARS = 160  # ...and below this, once furniture is stripped
MIN_IMAGE_AREA = 0.15      # image coverage that makes OCR worth the time
OCR_DPI = 400

# Running headers and the Delhi High Court digital-signature block appear on
# every page of a court order, including pages whose real content is a scanned
# table.  They must not count as "this page already has text".
_FURNITURE = re.compile(
    r"This is a digitally signed order\.?"
    r"|The authenticity of the order[^\n]*"
    r"|The Order is downloaded from the [^\n]*"
    r"|Generated from eOffice by[^\n]*"
    r"|^[ \t]*Page[ \t]+\d+[ \t]+of[ \t]+\d+[ \t]*$"
    r"|^[ \t]*[A-Z][A-Z().\s]{0,24}?[ \t]*\d+/\d{4}[ \t]*(?:Page[ \t]+\d+[ \t]+of[ \t]+\d+)?[ \t]*$",
    re.I | re.M,
)


def meaningful_length(text: str) -> int:
    """Characters left once page furniture is removed."""
    return len(_FURNITURE.sub("", text or "").strip())


def content_lines(text: str) -> list[str]:
    """Page lines with running headers and signature blocks removed.

    Dropping the furniture puts the last real line of one page next to the
    first real line of the next, so a domain split across a page break
    ("https://popcornmovi" / "es.to") can be glued back together.
    """
    return [l for l in _FURNITURE.sub("", text or "").splitlines() if l.strip()]


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Page:
    number: int                       # 1-based
    text: str
    words: list[Word] = field(default_factory=list)
    ocr_engine: str = ""              # "" when the embedded text layer was used
    has_images: bool = False

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    @property
    def content(self) -> list[str]:
        """Lines with page furniture stripped - what the URL tokeniser reads."""
        return content_lines(self.text)

    @property
    def ocred(self) -> bool:
        return bool(self.ocr_engine)


class Document:
    """Lazy, cached page access for one blocking order PDF."""

    def __init__(self, path: str | Path, *, use_ocr: bool = True, tesseract_path: str = ""):
        self.path = Path(path)
        self.use_ocr = use_ocr
        self.tesseract_path = tesseract_path
        self._doc = pymupdf.open(self.path)
        self._cache: dict[int, Page] = {}
        self._tables: dict[int, list[list[list[str]]]] = {}

    def __len__(self) -> int:
        return self._doc.page_count

    def close(self) -> None:
        try:
            self._doc.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- pages

    def page(self, number: int) -> Page:
        """1-based page access."""
        if number in self._cache:
            return self._cache[number]
        raw = self._doc[number - 1]
        try:
            text = raw.get_text("text") or ""
        except Exception:  # noqa: BLE001 - a broken page must not kill the run
            text = ""
        empty = len(text.strip()) < MIN_TEXT_CHARS
        thin = meaningful_length(text) < MIN_MEANINGFUL_CHARS
        sparse = empty or thin
        try:
            has_images = bool(raw.get_images(full=False))
        except Exception:  # noqa: BLE001
            has_images = False
        # Some orders (May 25_ pages 9-12) carry no text layer *and* no images:
        # the page is drawn as vector paths.  Those still need OCR, so treat a
        # page with lots of drawing operations as scanned content.
        # A page can carry a thin text layer (running header + signature block)
        # over a scanned table - May 25_ page 4 hides nine domains that way.
        has_content = has_images and _image_coverage(raw) >= MIN_IMAGE_AREA
        if sparse and not has_content and empty:
            try:
                has_content = len(raw.get_drawings()) > 20
            except Exception:  # noqa: BLE001
                has_content = False

        engine = ""
        words: list[Word] = []
        if sparse and has_content and self.use_ocr:
            png = self._render(raw)
            result = ocr_mod.ocr_png(png, self.tesseract_path)
            if result.ok:
                repaired, _ = ocr_mod.repair_lines(result.text.splitlines())
                # Append rather than replace: the text layer may still hold the
                # heading that tells the locator what this page is.
                text = (text + "\n" + "\n".join(repaired)) if not empty else "\n".join(repaired)
                engine = result.engine
                words = [Word(w.text, w.x0, w.y0, w.x1, w.y1) for w in result.words]
        if not words:
            words = self._words(raw)

        page = Page(number, text, words, engine, has_images)
        self._cache[number] = page
        return page

    def pages(self, start: int = 1, stop: int | None = None):
        stop = stop or len(self)
        for i in range(start, min(stop, len(self)) + 1):
            yield self.page(i)

    def force_ocr(self, number: int) -> Page:
        """Re-read a page through OCR even when it has a text layer."""
        raw = self._doc[number - 1]
        result = ocr_mod.ocr_png(self._render(raw), self.tesseract_path)
        repaired, _ = ocr_mod.repair_lines(result.text.splitlines())
        page = Page(
            number,
            "\n".join(repaired),
            [Word(w.text, w.x0, w.y0, w.x1, w.y1) for w in result.words],
            result.engine,
            True,
        )
        self._cache[number] = page
        return page

    def tables(self, number: int) -> list[list[list[str]]]:
        """Rows of cell strings for each table PyMuPDF finds on the page."""
        if number in self._tables:
            return self._tables[number]
        self._tables[number] = self._find_tables(number)
        return self._tables[number]

    def _find_tables(self, number: int) -> list[list[list[str]]]:
        try:
            finder = self._doc[number - 1].find_tables()
        except Exception:  # noqa: BLE001
            return []
        out = []
        for table in finder.tables:
            try:
                out.append([[c or "" for c in row] for row in table.extract()])
            except Exception:  # noqa: BLE001
                continue
        return out

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _render(raw) -> bytes:  # noqa: D401
        pix = raw.get_pixmap(dpi=OCR_DPI)
        return pix.tobytes("png")

    @staticmethod
    def _words(raw) -> list[Word]:
        try:
            return [
                Word(w[4], w[0], w[1], w[2], w[3])
                for w in raw.get_text("words")
            ]
        except Exception:  # noqa: BLE001
            return []


def column_bands(words: list[Word], gap: float = 18.0) -> list[tuple[float, float]]:
    """Cluster word x-ranges into column bands.

    Used to isolate the 'Rogue Domain Names' column from the neighbouring
    'Registrar' column in tabular annexures.
    """
    if not words:
        return []
    spans = sorted((w.x0, w.x1) for w in words)
    bands: list[list[float]] = [[spans[0][0], spans[0][1]]]
    for x0, x1 in spans[1:]:
        if x0 - bands[-1][1] > gap:
            bands.append([x0, x1])
        else:
            bands[-1][1] = max(bands[-1][1], x1)
    return [(a, b) for a, b in bands]


def words_in_band(words: list[Word], band: tuple[float, float]) -> list[str]:
    """Reading-order lines built only from words inside one column band."""
    lo, hi = band
    sel = [w for w in words if w.x0 >= lo - 1 and w.x1 <= hi + 1]
    if not sel:
        return []
    rows: list[list[Word]] = []
    for w in sorted(sel, key=lambda w: (round(w.y0, 1), w.x0)):
        if rows and abs(rows[-1][0].y0 - w.y0) <= 4.0:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [" ".join(w.text for w in sorted(r, key=lambda w: w.x0)) for r in rows]


def _image_coverage(raw) -> float:
    """Fraction of the page covered by embedded images."""
    try:
        page_area = abs(raw.rect.get_area())
        if page_area <= 0:
            return 0.0
        total = 0.0
        for info in raw.get_image_info():
            bbox = info.get("bbox")
            if not bbox:
                continue
            total += abs((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        return min(total / page_area, 1.0)
    except Exception:  # noqa: BLE001
        return 0.0
