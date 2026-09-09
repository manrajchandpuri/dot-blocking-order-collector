"""Parse the DoT covering letter on page 1.

The covering letter is the authority for this whole pipeline.  It states the
order's own date, whether the action is blocking or unblocking, *how many*
websites are involved, and *where* the list of them lives.  Extracting URLs
without it goes badly wrong: ``37_Blocking order dated 20 May 2025.pdf`` encloses
a court annexure listing ~15 Facebook URLs, of which exactly one -
``https://backpotin.com``, named in the letter - is actually to be blocked.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .urls import Candidate, candidates_from_text

# ---------------------------------------------------------------- regexes

# The letterhead date can be spaced oddly ("Date: 07-08- 2026") or be missing
# its day altogether ("Date: -07-2026", attachment 81855).
_DATE_PATTERNS = (
    re.compile(
        r"\bDated?\s*:?\s*(\d{1,2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{4})\b", re.I
    ),
)

# Where the letterhead ends.  Searching past it finds the *court order's* date
# ("...order dated 09.04.2025") instead of the DoT instruction's own date.
_HEAD_END = re.compile(r"\bSubject\s*:|\bTo\b[,\s]+All\s+Licensees", re.I)
_HEAD_FALLBACK = 900
_DATE_WORDY = re.compile(
    r"\bDated?\s*:?\s*(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r",?\s+(\d{4})",
    re.I,
)

# The DoT letter states the count in a bracket, in a startling number of
# shapes.  Everything before the "nos"/"No" marker is the arithmetic; anything
# after it is a breakdown by defendant and must be ignored:
#   [ 37 nos.]  [12 nos]  [ 01 No. ]  [2+9=11 nos]  [8+10=18 nos]
#   [402 Nos]   [102 Nos no.]   [05 Nos (Defendant No.1)]
#   [198 Nos. (132 (Defendant No. 01 to 30) + 66 (Defendant No.63 to 128)]
_COUNT_BRACKET_RE = re.compile(
    r"\[([^\]]{0,120}?)\b(?:[Nn]os?\b\.?|in\s+number)([^\]]{0,120})\]", re.I
)
# The 2026 letters sometimes drop the bracket entirely: "blocking access to the
# 586 domains/URLs as listed in the enclosed list", "the 5 no. of website".
_COUNT_BARE_RE = re.compile(
    r"\b(\d{1,5})\s*(?:nos?\.?\s*(?:of\s+)?)?"
    r"(?:domains?|websites?|urls?|domain\(s\)|website\(s\)|url\(s\))"
    r"(?:\s*/\s*(?:domains?|websites?|urls?))?",
    re.I,
)

_BODY_START = re.compile(
    r"(?:Please|Kindly)\s+(?:find|refer|note)|In\s+compliance\s+with\s+the\s+said", re.I
)
_BODY_END = re.compile(r"\bEncl\s*[:.]|\bCopy\s+to\s*:|\bDirector\s*\(DS", re.I)

_SUBJECT_RE = re.compile(r"Subject\s*:\s*(.+?)(?=\s*(?:Please|Kindly|In compliance)\b)", re.I | re.S)
# Case numbers seen across the corpus: "CS(COMM) 420/2023", "CS (COMM) 87 of
# 2023", "COMIPS (L) No. 18000 of 2026", "S.B. Civil Writ Petition No.
# 19457/2026", "Writ-A No. 30480 of 2026".  Anchored on the colon that ends the
# subject's case reference.
_CASE_RE = re.compile(
    r"\b((?:CS|C\.S\.|COMIPS|CO|W\.?P|S\.?B\.?|OMP|CM|FAO|RFA|TM|IPRS|Writ|Suit|OS|TA)"
    r"[^:]{0,70}?(?:No\.?\s*)?\d+\s*(?:/|of)\s*\d{2,4})\s*[:\-]",
    re.I,
)
# "in the Hon'ble High Court of Delhi", "before the High Court at CALCUTTA",
# "in the Hon'ble Bombay High Court" - the place can lead or trail.
_COURT_RE = re.compile(
    r"(?:in|before|at)\s+the\s+(?:Hon[’'`]?ble\s+)*"
    r"((?:[A-Z][\w’'.\-]{1,20}\s+){0,3}"
    r"(?:High\s+Court|Supreme\s+Court|District\s+Court|Commercial\s+Court|Court)"
    r"[^.,;]{0,70})",
    re.I,
)

_LIST_LABELS = (
    re.compile(r"\bDocument\s*[-–]?\s*([A-Z0-9]{1,3})\b"),
    re.compile(r"\bAnnexure\s*[-–]?\s*([A-Z0-9]{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3}(?:st|nd|rd|th)\s+(?:and\s+\d{1,3}(?:st|nd|rd|th)\s+)?"
               r"(?:Addl\.?|Additional)\s+Lists?)", re.I),
    re.compile(r"\b(\d{1,3}(?:st|nd|rd|th)\s+Lists?\s+of\s+websites)", re.I),
    re.compile(r"\b(Schedule)\b", re.I),
    re.compile(r"\b(enclosed\s+list|list\s+of\s+these\s+domains|enclosed\s+herewith)\b", re.I),
)

_PARA_REF_RE = re.compile(
    r"\bpara(?:s|graph|graphs)?\.?\s*\(?s?\)?\s*"
    r"(\d{1,3}\s*(?:\([^)]{1,20}\))?"
    r"(?:\s*(?:to|and|&|,)\s*\d{1,3}\s*(?:\([^)]{1,20}\))?)*)",
    re.I,
)

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}


@dataclass
class Letter:
    """Everything page 1 tells us."""

    order_date: dt.date | None = None
    date_source: str = ""              # "letter" | "title" | "published"
    case_no: str = ""
    court: str = ""
    subject: str = ""
    body: str = ""
    directive: str = ""
    action: str = "block"              # "block" | "unblock"
    declared_count: int | None = None
    count_expression: str = ""
    inline_urls: list[Candidate] = field(default_factory=list)
    list_labels: list[str] = field(default_factory=list)
    court_para_refs: list[str] = field(default_factory=list)
    points_at_court_order: bool = False

    @property
    def has_enclosed_list(self) -> bool:
        return bool(self.list_labels) and not self.points_at_court_order


def parse_letter(page_text: str) -> Letter:
    """Parse the covering letter from page 1's raw text."""
    flat = " ".join(page_text.split())
    letter = Letter()

    letter.order_date = _find_date(flat)
    if letter.order_date:
        letter.date_source = "letter"

    m = _SUBJECT_RE.search(flat)
    letter.subject = (m.group(1).strip() if m else "")[:400]

    cm = _CASE_RE.search(letter.subject or flat)
    letter.case_no = cm.group(1).strip() if cm else ""
    tm = _COURT_RE.search(letter.subject or flat)
    letter.court = " ".join((tm.group(1) if tm else "").split()).rstrip(" .")

    letter.body = _body(flat)
    letter.directive = _directive(letter.body)
    letter.action = "unblock" if re.search(r"\bunblock", letter.directive or letter.body, re.I) else "block"

    letter.declared_count, letter.count_expression = _declared_count(letter.body)
    letter.inline_urls = _inline_urls(letter.directive or letter.body)
    letter.list_labels = _list_labels(letter.body)
    letter.court_para_refs = _para_refs(letter.directive or letter.body)
    letter.points_at_court_order = bool(
        re.search(
            r"(?:mentioned|enumerated|specified|listed|given|described)\s+in\s+"
            r"(?:the\s+)?para(?:s|graph)?[^.]{0,60}\bof\s+the\s+said\s+(?:Court\s+)?order",
            letter.directive or letter.body,
            re.I,
        )
    )
    return letter


# ---------------------------------------------------------------- internals


def _letterhead(flat: str) -> str:
    m = _HEAD_END.search(flat)
    return flat[: m.start()] if m else flat[:_HEAD_FALLBACK]


def _find_date(flat: str) -> dt.date | None:
    head = _letterhead(flat)
    for pat in _DATE_PATTERNS:
        m = pat.search(head)
        if m:
            try:
                return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                continue
    m = _DATE_WORDY.search(head)
    if m:
        try:
            return dt.date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        except (ValueError, KeyError):
            pass
    return None


def _body(flat: str) -> str:
    start = _BODY_START.search(flat)
    s = start.start() if start else 0
    end = _BODY_END.search(flat, s)
    return flat[s : end.start() if end else len(flat)].strip()


def _directive(body: str) -> str:
    """The sentence(s) that actually name what is to be blocked."""
    # Prefer the numbered paragraph containing "blocking"/"unblocking".
    chunks = re.split(r"(?<=[.\]])\s+(?=\d{1,2}\.\s)", body)
    hits = [c for c in chunks if re.search(r"\b(?:un)?block", c, re.I)]
    if hits:
        # Drop the boilerplate "In view of the above ... take immediate necessary
        # action ... as above" paragraph, which names nothing.
        specific = [
            c for c in hits
            if not re.search(r"In\s+view\s+of\s+the\s+above", c, re.I)
            or re.search(r"\[|https?://", c)
        ]
        return (specific or hits)[0].strip()
    return body


def _declared_count(body: str) -> tuple[int | None, str]:
    for m in _COUNT_BRACKET_RE.finditer(body):
        value = _parse_count_expression(m.group(1))
        if value is not None:
            return value, m.group(0)
    m = _COUNT_BARE_RE.search(body)
    if m:
        try:
            return int(m.group(1)), m.group(0)
        except ValueError:
            pass
    return None, ""


def _parse_count_expression(head: str) -> int | None:
    """The arithmetic that precedes the "nos" marker inside the bracket."""
    head = head.strip()
    if not head or not re.search(r"\d", head):
        return None
    if not re.fullmatch(r"[\d\s+=,.]+", head):
        return None                       # prose, not a count
    if "=" in head:                       # "2+9=11" - the total is stated
        tail = head.split("=")[-1]
        digits = re.findall(r"\d+", tail)
        return int(digits[0]) if digits else None
    if "+" in head:                       # "8+10"
        return sum(int(d) for d in re.findall(r"\d+", head)) or None
    digits = re.findall(r"\d+", head)
    return int(digits[0]) if digits else None


def _inline_urls(text: str) -> list[Candidate]:
    """URLs named directly in the directive - these ARE the answer when present."""
    return candidates_from_text(text)


def _list_labels(body: str) -> list[str]:
    out: list[str] = []
    for pat in _LIST_LABELS:
        for m in pat.finditer(body):
            label = m.group(0).strip()
            if label.lower() not in {l.lower() for l in out}:
                out.append(label)
    return out


def _para_refs(text: str) -> list[str]:
    out = []
    for m in _PARA_REF_RE.finditer(text):
        ref = " ".join(m.group(1).split())
        if ref not in out:
            out.append(ref)
    return out
