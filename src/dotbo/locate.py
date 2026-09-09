"""Work out which pages of a blocking order actually hold the list of URLs.

A DoT blocking order is a bundle: the covering letter, the annexure listing the
websites, forwarded emails, the court order itself, plaintiff affidavits and
evidence screenshots, and a standard DoT annexure about traceroutes.  Only one
of those is the list we want, so every page gets classified first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdftext import Document, Page, column_bands, words_in_band
from .urls import Candidate, candidates_from_lines, drop_fragments, looks_like_target

# ---------------------------------------------------------------- page kinds

MAX_RUN_PAGES = 40
MAX_TABLE_COLUMNS = 6

# Reading strategies, best-first.  "col:N" reads only the Nth column of every
# table on the page - that is what separates the rogue domains from the
# registrar names beside them, and the defendant's websites from the
# plaintiff's in a court comparison table.
STRATEGIES = (
    ("plain", 0.0),
    ("bands", -1.0),
    ("tables", -2.0),
) + tuple((f"col:{i}", -3.0 - i * 0.1) for i in range(MAX_TABLE_COLUMNS))

CHEAP_STRATEGIES = (("plain", 0.0), ("bands", -1.0))

LETTER = "letter"
LIST = "list"
COURT = "court"
EMAIL = "email"
BOILERPLATE = "boilerplate"
OTHER = "other"

_COURT_MARKERS = (
    re.compile(r"IN\s+THE\s+(HIGH|SUPREME|DISTRICT)\s+COURT", re.I),
    re.compile(r"\bO\s*R\s*D\s*E\s*R\b"),
    re.compile(r"HON'?BLE\s+(MR|MS|MRS|DR)\.?\s+JUSTICE", re.I),
    re.compile(r"This\s+is\s+a\s+digitally\s+signed\s+order", re.I),
    re.compile(r"downloaded\s+from\s+the\s+DHC\s+Server", re.I),
    re.compile(r"\.\.\.\.\.(Plaintiff|Defendant)", re.I),
)

_EMAIL_MARKERS = (
    re.compile(r"Forwarded\s+message", re.I),
    re.compile(r"^\s*From:\s", re.I | re.M),
    re.compile(r"\babout:blank\b", re.I),
    re.compile(r"\bAttachment\(s\)", re.I),
)

_BOILERPLATE_MARKERS = (
    re.compile(r"more\s+than\s+2700\s+ISPs", re.I),
    re.compile(r"trace\s*route\s+of\s+the\s+web\s+server", re.I),
    re.compile(r"Allocation\s+of\s+Business\s+Rules", re.I),
)

_LIST_HEADINGS = (
    re.compile(r"Website\(s\)\s*/\s*domain\(s\)", re.I),
    re.compile(r"\bLIST\s+OF\s+(DOMAIN|WEBSITE|URL)", re.I),
    re.compile(r"\bRogue\s+Domain\s+Names?\b", re.I),
    re.compile(r"\b(?:Additional|Addl\.?)\s+List\b", re.I),
    re.compile(r"^\s*Websites?\s*$", re.I | re.M),
    re.compile(r"^\s*Websites?\s*\(", re.I | re.M),
    re.compile(r"^\s*List\s*[-–]\s*\d+\b", re.I | re.M),
    re.compile(r"\bDocument\s*[-–]?\s*[A-Z0-9]{1,3}\s*:", re.I),
    re.compile(r"\bAnnexure\s*[-–]?\s*[A-Z0-9]{0,3}\s*$", re.I | re.M),
    re.compile(r"S\.?\s*No\.?\s*\|?\s*(Domain|Website|URL)", re.I),
    re.compile(r"\bIMPUGNED\s+(DOMAIN|WEBSITE)", re.I),
    re.compile(r"\bSchedule\b", re.I),
)


@dataclass
class PageInfo:
    page: Page
    kind: str
    heading_hit: bool
    label_hit: bool
    candidates: list[Candidate] = field(default_factory=list)
    raw_mentions: int = 0            # before de-duplication

    @property
    def number(self) -> int:
        return self.page.number

    @property
    def density(self) -> float:
        lines = [l for l in self.page.lines if l.strip()]
        return len(self.candidates) / max(len(lines), 1)


def classify(page: Page, labels: list[str]) -> tuple[str, bool, bool]:
    text = page.text
    heading = any(p.search(text) for p in _LIST_HEADINGS)
    label_hit = any(_label_matches(text, lbl) for lbl in labels)

    if page.number == 1:
        return LETTER, heading, label_hit
    if any(p.search(text) for p in _BOILERPLATE_MARKERS):
        return BOILERPLATE, heading, label_hit
    court_hits = sum(bool(p.search(text)) for p in _COURT_MARKERS)
    email_hits = sum(bool(p.search(text)) for p in _EMAIL_MARKERS)
    if label_hit:
        # The letter named this exact list; annexures are routinely bound
        # inside the court order (May 20_ has "Annexure - A" on court pages).
        return LIST, heading, label_hit
    if heading and court_hits < 2:
        # A list page can sit inside an affidavit; the heading wins unless the
        # page is unmistakably a court order body.
        return LIST, heading, label_hit
    if email_hits >= 1 and court_hits == 0:
        return EMAIL, heading, label_hit
    if court_hits >= 1:
        return COURT, heading, label_hit
    return OTHER, heading, label_hit


def _label_matches(text: str, label: str) -> bool:
    label = label.strip()
    if not label or len(label) < 4:
        return False
    if re.search(r"enclos|list of these domains", label, re.I):
        return False          # too generic to locate anything
    # "Annexure- A" in the letter vs "Annexure - A" on the page: match on the
    # sequence of word characters, with any punctuation/space between them.
    tokens = [re.escape(t) for t in re.findall(r"\w+", label)]
    if not tokens:
        return False
    pattern = r"[\s\-–—:.]*".join(tokens)
    return bool(re.search(pattern, text, re.I))


def survey(doc: Document, labels: list[str]) -> list[PageInfo]:
    """Classify every page and pre-extract its URL candidates."""
    out: list[PageInfo] = []
    for page in doc.pages():
        kind, heading, label_hit = classify(page, labels)
        cands = candidates_from_lines(page.content)
        raw = _raw_mentions(page.content, {c.key for c in cands})
        out.append(PageInfo(page, kind, heading, label_hit, cands, raw))
    return out


_MARKER_ALONE = re.compile(r"^\s*(\d{1,4})\s*[.)]\s*$")
_MARKER_INLINE = re.compile(r"^\s*(\d{1,4})\s*[.)]\s+(\S+)")


def entry_markers(lines: list[str]) -> int:
    """How many numbered list entries this text holds.

    DoT counts *entries*, and an annexure can list the same URL twice - order
    83543 declares 402 websites but only 369 of them are distinct.  Counting the
    numbering therefore matches the declaration where a URL count cannot.
    """
    seen: set[int] = set()
    for i, line in enumerate(lines):
        m = _MARKER_ALONE.match(line)
        if m:
            nxt = next((l for l in lines[i + 1 : i + 3] if l.strip()), "")
            if looks_like_target(nxt):
                seen.add(int(m.group(1)))
            continue
        m = _MARKER_INLINE.match(line)
        if m and looks_like_target(m.group(2)):
            seen.add(int(m.group(1)))
    return len(seen)


def _raw_mentions(lines: list[str], kept: set[str]) -> int:
    """URL occurrences before de-duplication - matches how DoT counts rows.

    Counted through the same de-wrapping pass as the extraction itself, and
    restricted to the URLs we actually report.  Reading the lines one at a time
    instead would miss every entry whose domain wraps, which is what made order
    82667 look five short of its declared 586.
    """
    return sum(1 for c in candidates_from_lines(lines, dedupe=False) if c.key in kept)


# ---------------------------------------------------------------- regions


@dataclass
class Region:
    """A contiguous page span plus how we read it."""

    pages: tuple[int, ...]
    strategy: str                 # "plain" | "bands" | "tables"
    origin: str                   # "annexure" | "court_order" | "document"
    prior: float = 0.0


def regions(infos: list[PageInfo], letter_paras: list[str]) -> list[Region]:
    """Candidate page spans, best-prior first."""
    by_num = {i.number: i for i in infos}
    out: list[Region] = []

    def add(pages, origin, prior, strategies=STRATEGIES):
        pages = tuple(p for p in pages if p in by_num)
        if not pages:
            return
        for strat, bump in strategies:
            out.append(Region(pages, strat, origin, prior + bump))

    # 1. Runs of pages that look like the annexure, anchored on a heading/label.
    anchors = [i.number for i in infos if i.kind == LIST]
    runs: list[list[int]] = []
    for anchor in anchors:
        run = [anchor]
        n = anchor + 1
        # An annexure named by the letter is often bound inside the court order
        # and runs across pages carrying court headers (May 20_, Annexure - A on
        # pages 12-16), so those continue the run.  A fresh heading - the start
        # of "Annexure - B" - ends it.
        # A fresh heading part-way through (the start of "Annexure - B") does not
        # end the run here: every prefix of the run is offered as its own
        # candidate below, so the count oracle picks the right cut-off.
        # Court pages continue the run too: annexures are routinely bound inside
        # the order (May 20_), and a long list simply runs on across pages that
        # still carry the court's running header.
        while (
            n in by_num
            and by_num[n].kind in (LIST, OTHER, COURT)
            and by_num[n].candidates
            and len(run) < MAX_RUN_PAGES
        ):
            run.append(n)
            n += 1
        prior = 40.0 + (10.0 if anchor <= 3 else 0.0)
        if by_num[anchor].label_hit:
            prior += 25.0
        # Every prefix of the run: a two-list annexure (May 10_, 11_) occupies
        # pages 2-3 while the run keeps going into the covering emails.  Only
        # the whole run and the anchor page get the full strategy set; the
        # intermediate cut-offs stay cheap.
        for k in range(len(run), 0, -1):
            strategies = STRATEGIES if (k in (len(run), 1) or len(run) <= 8) else CHEAP_STRATEGIES
            add(run[:k], "annexure", prior - (len(run) - k) * 0.5, strategies)
        runs.append(run)

    # 2b. Every individual page that has URLs and is not obviously wrong.
    for info in infos:
        if info.kind in (LETTER, BOILERPLATE) or not info.candidates:
            continue
        prior = {LIST: 30.0, OTHER: 14.0, EMAIL: 2.0, COURT: 4.0}.get(info.kind, 5.0)
        if info.number <= 3:
            prior += 12.0
        elif info.number <= 5:
            prior += 4.0
        add([info.number], "annexure" if info.kind == LIST else "document", prior)

    # 3. Every annexure block at once.  An order can carry the same list twice -
    #    order 82667 has it on pages 2-25 and again on 42-56 inside the forwarded
    #    email.  Built from the anchored runs, so stray URLs in the court order
    #    stay out.
    blocks = sorted({p for run in runs for p in run})
    if len(runs) > 1 and len(blocks) > 1:
        add(blocks, "annexure", 18.0 - _gap_penalty(blocks))

    # 4. Every list-ish page together.  The fallback for a document with no
    #    heading to anchor on at all (May 38_).  Penalised by how scattered it
    #    is: a contiguous annexure must always outrank a spray of pages that
    #    happens to total the declared number, which is what made May 2_ pick
    #    pages 2, 5, 9 and 12.
    #    It carries "document" trust rather than "annexure" trust, so a count
    #    that lands on it exactly counts for much less than the same count on a
    #    span anchored by a real heading.
    loose = [i.number for i in infos if i.kind in (LIST, OTHER) and i.candidates]
    if len(loose) > 1 and loose != blocks:
        add(loose, "document", 16.0 - _gap_penalty(loose))

    # 5. The court order body - only when the letter points into it.
    court_pages = [i.number for i in infos if i.kind == COURT and i.candidates]
    if court_pages and letter_paras:
        add(court_pages, "court_order", 10.0)

    # 6. Last resort: everything with URLs except the letter.  Only offered when
    #    nothing looked like a list, because this span drags in the court order
    #    and the covering emails.
    if not anchors:
        everything = [i.number for i in infos if i.kind != LETTER and i.candidates]
        if everything:
            add(everything, "document", 1.0)

    out.sort(key=lambda r: -r.prior)
    return out


def _gap_penalty(pages: list[int]) -> float:
    """How much to dock a region for not being one continuous span.

    A real annexure runs across consecutive pages.  A set stitched together from
    pages scattered through the bundle is far more likely to be a coincidence
    than a list, so each break costs it.
    """
    if len(pages) < 2:
        return 0.0
    gaps = sum(1 for a, b in zip(pages, pages[1:]) if b != a + 1)
    return min(gaps * 3.0, 14.0)


def read_region(doc: Document, region: Region) -> tuple[list[Candidate], int, int]:
    """Apply the region's strategy.

    Returns ``(deduped candidates, raw mentions, numbered entries)``.
    """
    lines: list[str] = []
    for number in region.pages:
        page = doc.page(number)
        if region.strategy == "plain":
            lines.extend(page.content)
        elif region.strategy == "bands":
            lines.extend(_band_lines(page))
        elif region.strategy.startswith("col:"):
            lines.extend(_column_lines(doc, number, int(region.strategy[4:])))
        else:
            lines.extend(_table_lines(doc, number) or page.content)
    cands = drop_fragments(candidates_from_lines(lines))
    kept = {c.key for c in cands}
    return cands, _raw_mentions(lines, kept), entry_markers(lines)


def _band_lines(page: Page) -> list[str]:
    """Only the column bands that actually contain URLs.

    This is what keeps ``GoDaddy.com, LLC`` in the Registrar column out of the
    results while keeping the rogue domains beside it.
    """
    bands = column_bands(page.words)
    if len(bands) <= 1:
        return page.content
    out: list[str] = []
    for band in bands:
        band_lines = words_in_band(page.words, band)
        if candidates_from_lines(band_lines):
            out.extend(band_lines)
    return out or page.content


def _column_lines(doc: Document, number: int, index: int) -> list[str]:
    """Cells from one column of every table on the page."""
    out: list[str] = []
    for table in doc.tables(number):
        for row in table:
            if index < len(row) and row[index]:
                out.extend(str(row[index]).splitlines())
    return out


def _table_lines(doc: Document, number: int) -> list[str]:
    out: list[str] = []
    for table in doc.tables(number):
        for row in table:
            for cell in row:
                if cell:
                    out.extend(str(cell).splitlines())
    return out
