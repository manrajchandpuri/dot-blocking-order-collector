"""Pick the right set of blocked URLs out of a blocking order.

The covering letter almost always declares how many websites are involved
(``[ 37 nos.]``, ``[2+9=11 nos]``, ``[ 01 No. ]``).  Across the 42 May 2025
orders that declaration agrees with the hand-built reference for 41 of them, so
extraction is framed as *constrained search*: generate several candidate URL
sets from different page spans and reading strategies, then keep the one whose
size matches what the letter says.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import locate
from .letter import Letter, parse_letter
from .pdftext import Document
from .urls import Candidate, drop_fragments

HIGH, MEDIUM, LOW = "high", "medium", "low"


@dataclass
class Extraction:
    urls: list[str] = field(default_factory=list)
    confidence: str = LOW
    source: str = "none"            # letter | annexure | court_order | document
    declared_count: int | None = None
    raw_mentions: int = 0
    entries: int = 0                # numbered list entries in the source
    pages: tuple[int, ...] = ()
    strategy: str = ""
    ocr_pages: tuple[int, ...] = ()
    ocr_engine: str = ""
    merged_from: tuple[str, ...] = ()   # strategies unioned, when more than one
    corroborated: bool = False
    corroboration: str = ""             # how the result was confirmed
    notes: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.urls)

    @property
    def count_matches(self) -> bool | None:
        if self.declared_count is None:
            return None
        return self.declared_count in (self.count, self.raw_mentions, self.entries)


def extract(doc: Document, letter: Letter | None = None) -> tuple[Letter, Extraction]:
    """Full extraction for one already-open blocking order."""
    letter = letter or parse_letter(doc.page(1).text)

    # 1. The letter names the URLs outright (May 4, 5, 23, 26, 37, 40).
    inline = letter.inline_urls
    if inline and letter.declared_count in (None, len(inline)):
        return letter, Extraction(
            urls=[c.text for c in inline],
            confidence=HIGH,
            source="letter",
            declared_count=letter.declared_count,
            raw_mentions=len(inline),
            pages=(1,),
            strategy="letter",
            corroborated=True,
            corroboration="named in the DoT covering letter",
            notes=["URLs named directly in the DoT covering letter."],
        )

    # 2. Otherwise search the bundle.
    infos = locate.survey(doc, letter.list_labels)
    cands = locate.regions(infos, letter.court_para_refs)

    readings: list[tuple[float, locate.Region, list[Candidate], int, int]] = []
    for region in cands:
        urls, raw, entries = locate.read_region(doc, region)
        if not urls:
            continue
        score = _score(len(urls), raw, entries, letter.declared_count, region)
        readings.append((score, region, urls, raw, entries))

    best = _choose(readings, letter, doc)

    if best is None:
        return letter, Extraction(
            confidence=LOW,
            source="none",
            declared_count=letter.declared_count,
            notes=["No website list could be located in this order."],
        )

    best.confidence = _confidence(best, letter)
    best.corroborated, best.corroboration = _corroboration(best, readings)
    best.ocr_pages = tuple(
        i.number for i in infos if i.page.ocred and i.number in best.pages
    )
    # Only report the engine when OCR actually contributed to *this* result;
    # other pages of the bundle may have been OCR'd and thrown away.
    engines = {
        i.page.ocr_engine for i in infos if i.page.ocred and i.number in best.pages
    }
    best.ocr_engine = next(iter(engines), "")
    if best.ocr_pages:
        best.notes.append(
            f"OCR ({best.ocr_engine}) used on page(s) "
            + ", ".join(str(p) for p in best.ocr_pages)
        )
    if best.source == "court_order":
        best.notes.append("URLs taken from the court order/judgement (no list annexed).")
    if best.merged_from:
        best.notes.append(
            "No single reading matched the declared count, so the readings were "
            "merged (" + ", ".join(best.merged_from) + ")."
        )
    if best.declared_count is not None and best.count_matches is False:
        best.notes.append(
            f"Found {best.count} URLs against a declared {best.declared_count}."
        )
    elif best.declared_count is not None and best.count != best.declared_count:
        best.notes.append(
            f"The DoT letter declares {best.declared_count} entries; "
            f"{best.count} of them are distinct."
        )
    return letter, best


# How much a count match is worth, by how list-like the span is.  A scattered
# span that happens to total the right number is not evidence of anything: order
# 82667's court-order pages hit 586 exactly while being obviously wrong.
_MATCH_BONUS = {"annexure": 1000.0, "court_order": 800.0, "document": 450.0}


def _choose(readings, letter: Letter, doc: Document) -> Extraction | None:
    """Pick the reading to ship.

    An exact agreement with the DoT letter wins outright - that is what keeps
    the column-isolation results right (May 21_ dropping the registrar column,
    22_ taking only the defendant's column, August 82667 landing on col:0).

    When nothing agrees, completeness wins instead: the readings of the best
    region are unioned rather than one of them being picked, because a blocked
    URL missing from the compilation is worse than a stray one.
    """
    if not readings:
        return None
    readings = sorted(readings, key=lambda r: -r[0])
    score, region, urls, raw, entries = readings[0]

    best = Extraction(
        urls=[c.text for c in urls],
        source=region.origin,
        declared_count=letter.declared_count,
        raw_mentions=raw,
        entries=entries,
        pages=region.pages,
        strategy=region.strategy,
    )
    if best.count_matches:
        return best

    # Union every reading of the same page span, keeping first-seen order.
    same_span = [r for r in readings if r[1].pages == region.pages]
    if len(same_span) < 2:
        return best
    merged: list[Candidate] = []
    seen: set[str] = set()
    for _s, _r, cand_urls, _raw, _ent in same_span:
        for c in cand_urls:
            if c.key not in seen:
                seen.add(c.key)
                merged.append(c)
    merged = drop_fragments(merged)
    if len(merged) <= best.count:
        return best

    best.urls = [c.text for c in merged]
    best.raw_mentions = max(r[3] for r in same_span)
    best.entries = max(r[4] for r in same_span)
    best.strategy = region.strategy + "+union"
    best.merged_from = tuple(dict.fromkeys(r[1].strategy for r in same_span))
    return best


def _corroboration(ex: Extraction, readings) -> tuple[bool, str]:
    """Is this result confirmed by something other than itself?

    Three independent confirmations are accepted: the count the DoT letter
    declares, the annexure's own numbering, and two different reading strategies
    arriving at the same set of URLs.
    """
    if ex.declared_count is not None:
        if ex.declared_count == ex.count:
            return True, f"matches the {ex.declared_count} declared in the DoT letter"
        if ex.declared_count == ex.raw_mentions:
            return True, (
                f"{ex.raw_mentions} rows in the annexure match the declared "
                f"{ex.declared_count}; {ex.count} are distinct"
            )
        if ex.declared_count == ex.entries:
            return True, (
                f"the annexure numbers {ex.entries} entries, matching the "
                f"declared {ex.declared_count}; {ex.count} are distinct"
            )
    if ex.entries and ex.entries == ex.count:
        return True, f"the annexure's own numbering agrees ({ex.entries} entries)"

    # Two strategies reading the same pages and reaching the same set.
    same_span = [r for r in readings if r[1].pages == ex.pages]
    keysets = [frozenset(c.key for c in r[2]) for r in same_span]
    for i, a in enumerate(keysets):
        for b in keysets[i + 1 :]:
            if a and a == b:
                return True, "two independent readings of the same pages agree"
    return False, ""


def _score(
    n_unique: int, raw: int, entries: int, declared: int | None, region: locate.Region
) -> float:
    score = region.prior
    if declared is None:
        # Nothing to check against: prefer the closest, most list-like region.
        return score + min(n_unique, 30) * 0.5
    bonus = _MATCH_BONUS.get(region.origin, 450.0)
    if n_unique == declared:
        score += bonus
    elif entries == declared:
        # The annexure's own numbering agrees, even though it repeats URLs.
        score += bonus - 40.0
    elif raw == declared:
        # DoT counts mentions, the reference doc lists uniques (May 21_).
        score += bonus - 50.0
    else:
        gap = abs(n_unique - declared)
        score += max(0.0, 400.0 - gap * 40.0)
        if n_unique < declared:
            score -= 30.0           # a short list is worse than a slightly long one
    return score


def _confidence(ex: Extraction, letter: Letter) -> str:
    if ex.declared_count is not None:
        if ex.count_matches:
            return HIGH
        # A handful short of a large declared count is usually the annexure
        # repeating a URL rather than a miss, so say "check this" rather than
        # "this failed".
        margin = max(1, round(ex.declared_count * 0.02))
        if abs(ex.count - ex.declared_count) <= margin:
            return MEDIUM
        return LOW
    if ex.source == "annexure" and ex.count:
        return MEDIUM
    return LOW if not ex.count else MEDIUM
