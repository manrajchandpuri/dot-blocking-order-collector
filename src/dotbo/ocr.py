"""OCR for scanned pages, with post-OCR domain repair.

Primary engine is **Tesseract** (github.com/tesseract-ocr/tesseract), driven via
pytesseract.  Many DoT orders carry image-only pages -- e.g. pages 3-30 of
``34_Blocking order dated 23 May 2025.pdf`` and 30 pages of
``2_Blocking Order dated 5 August 2026.pdf`` -- and the annexure sometimes lands
on one of them.

If the Tesseract binary is not installed, we fall back to macOS's built-in
Vision OCR via ``ocrmac`` so the app still works out of the box; the QA report
records which engine produced each page.
"""
from __future__ import annotations

import functools
import io
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .urls import is_valid_domain, tlds

TESSERACT_SEARCH_PATHS = (
    "/opt/homebrew/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/local/bin/tesseract",
    "/usr/bin/tesseract",
)

INSTALL_HINT = (
    "Tesseract is not installed. Install it once with:\n"
    '  /bin/bash -c "$(curl -fsSL '
    'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"\n'
    "  brew install tesseract\n"
    "(or run scripts/install_ocr.sh). Until then macOS Vision OCR is used."
)


@dataclass
class OcrWord:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 0.0


@dataclass
class OcrResult:
    engine: str            # "tesseract" | "vision" | "none"
    text: str
    words: list[OcrWord]

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


# ---------------------------------------------------------------- engines


def find_tesseract(explicit: str = "") -> str:
    """Locate the tesseract binary, or return ''."""
    for cand in (explicit, os.environ.get("DOTBO_TESSERACT", "")):
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    found = shutil.which("tesseract")
    if found:
        return found
    for path in TESSERACT_SEARCH_PATHS:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return ""


@functools.lru_cache(maxsize=4)
def engine_status(explicit: str = "") -> tuple[str, str]:
    """(engine_name, human readable detail)."""
    binary = find_tesseract(explicit)
    if binary:
        try:
            out = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=20
            ).stdout.splitlines()
            ver = out[0].strip() if out else "tesseract"
            return "tesseract", f"{ver} ({binary})"
        except Exception:  # noqa: BLE001 - a broken binary is a missing binary
            pass
    try:
        import ocrmac  # noqa: F401

        return "vision", "macOS Vision OCR (ocrmac) - " + INSTALL_HINT.splitlines()[0]
    except Exception:  # noqa: BLE001
        return "none", INSTALL_HINT


def _tesseract_words(png: bytes, binary: str) -> OcrResult:
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = binary
    img = _preprocess(Image.open(io.BytesIO(png)))
    data = pytesseract.image_to_data(
        img,
        config="--oem 1 --psm 6 -c preserve_interword_spaces=1",
        output_type=pytesseract.Output.DICT,
    )
    words: list[OcrWord] = []
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        x, y = data["left"][i], data["top"][i]
        words.append(OcrWord(txt, x, y, x + data["width"][i], y + data["height"][i], conf))
    return OcrResult("tesseract", _words_to_text(words), words)


def _vision_words(png: bytes) -> OcrResult:
    from ocrmac import ocrmac
    from PIL import Image

    img = _preprocess(Image.open(io.BytesIO(png))).convert("RGB")
    w, h = img.size
    ann = ocrmac.OCR(img, framework="vision", recognition_level="accurate").recognize()
    words: list[OcrWord] = []
    for text, conf, bbox in ann:
        # Vision bboxes are normalised, origin bottom-left.
        bx, by, bw, bh = bbox
        x0, x1 = bx * w, (bx + bw) * w
        y0, y1 = (1 - by - bh) * h, (1 - by) * h
        for token in str(text).split():
            words.append(OcrWord(token, x0, y0, x1, y1, float(conf) * 100))
    return OcrResult("vision", _words_to_text(words), words)


def _preprocess(img):
    """Greyscale and stretch the contrast - and stop there.

    An earlier version also hard-thresholded the image, on the assumption that
    binarising helps OCR of dense domain tables.  Measured on the scanned
    annexure of order 84598 it does the opposite: thresholding yields 128 usable
    domains where plain greyscale yields 218.  Both engines here are neural
    (Tesseract's LSTM, and Vision), and both read anti-aliased greyscale better
    than the crushed bitmap.
    """
    from PIL import ImageOps

    return ImageOps.autocontrast(img.convert("L"))


def _words_to_text(words: list[OcrWord], line_tol: float = 8.0) -> str:
    """Rebuild reading-order text so the URL tokeniser sees proper lines."""
    if not words:
        return ""
    rows: list[list[OcrWord]] = []
    for w in sorted(words, key=lambda w: (w.y0, w.x0)):
        if rows and abs(rows[-1][0].y0 - w.y0) <= line_tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return "\n".join(" ".join(w.text for w in sorted(r, key=lambda w: w.x0)) for r in rows)


def ocr_png(png: bytes, tesseract_path: str = "") -> OcrResult:
    """OCR a rendered page image with whichever engine is available."""
    engine, _ = engine_status(tesseract_path)
    if engine == "tesseract":
        try:
            return _tesseract_words(png, find_tesseract(tesseract_path))
        except Exception:  # noqa: BLE001 - fall through to Vision
            engine = "vision"
    if engine == "vision":
        try:
            return _vision_words(png)
        except Exception:  # noqa: BLE001
            pass
    return OcrResult("none", "", [])


# ---------------------------------------------------------------- repair

# Confusions Tesseract makes on the low-contrast domain tables in these orders.
_CONFUSIONS = (
    ("l", "1"), ("1", "l"), ("I", "l"), ("l", "i"), ("O", "0"), ("0", "o"),
    ("o", "0"), ("5", "s"), ("s", "5"), ("8", "B"), ("B", "8"), ("rn", "m"),
    ("vv", "w"), ("cl", "d"), ("nn", "m"),
)


def repair_token(token: str) -> tuple[str, bool]:
    """Try to turn an OCR'd token into a valid domain.

    Returns ``(token, repaired)``.  Only edits that yield a real TLD are kept,
    so a token we cannot vouch for comes back untouched.
    """
    cleaned = re.sub(r"\s*([.\-/:])\s*", r"\1", token).strip()
    if is_valid_domain(cleaned):
        return cleaned, cleaned != token
    host = cleaned.split("/", 1)[0]
    if "." not in host:
        return token, False
    stem, _, tld = host.rpartition(".")
    low = tld.lower()
    if low in tlds():
        return token, False
    for a, b in _CONFUSIONS:
        cand_tld = low.replace(a, b)
        if cand_tld in tlds():
            fixed = cleaned.replace(host, f"{stem}.{cand_tld}", 1)
            if is_valid_domain(fixed):
                return fixed, True
    return token, False


def repair_lines(lines: list[str]) -> tuple[list[str], int]:
    """Apply :func:`repair_token` across whitespace-separated tokens."""
    out, fixes = [], 0
    for line in lines:
        parts = []
        for tok in line.split(" "):
            new, did = repair_token(tok) if "." in tok else (tok, False)
            fixes += did
            parts.append(new)
        out.append(" ".join(parts))
    return out, fixes
