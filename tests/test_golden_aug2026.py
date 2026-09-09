"""End-to-end check against the hand-built August 2026 reference folder.

The reference folder uses the other naming convention ("Blocking Order", no
zero-padded day), so files are matched on their *content* - byte size - and on
the day each numbered slot carries.
"""
import os
import re
from pathlib import Path

import pytest

from dotbo.config import RunOptions
from dotbo.pipeline import discover

REF = Path(
    "/Users/manrajsingh/Downloads/Example of Blocking Orders Package/Blocking Orders_August 2026"
)

pytestmark = pytest.mark.skipif(
    not REF.is_dir() or os.environ.get("DOTBO_SKIP_NETWORK") == "1",
    reason="reference folder or network unavailable",
)


def reference():
    """{index: (day, filesize)} from the reference filenames."""
    out = {}
    for path in REF.glob("*.pdf"):
        m = re.match(r"(\d+)_Blocking Order dated (\d+) August 2026\.pdf$", path.name)
        if m:
            out[int(m.group(1))] = (int(m.group(2)), path.stat().st_size)
    return out


def test_august_2026_matches_the_reference_compilation():
    expected = reference()
    assert len(expected) == 22, "reference folder should hold 22 orders"

    orders, _skipped = discover(RunOptions(year=2026, month=8))
    produced = {
        o.index: (o.order_date.day, Path(o.pdf_path).stat().st_size) for o in orders
    }

    assert len(produced) == len(expected), (
        f"produced {len(produced)} orders, reference has {len(expected)}"
    )
    mismatched = {i: (produced.get(i), expected[i]) for i in expected if produced.get(i) != expected[i]}
    assert not mismatched, f"slots differ from the reference: {mismatched}"


def test_filenames_use_the_may_convention():
    orders, _skipped = discover(RunOptions(year=2026, month=8))
    assert orders[0].filename == "1_Blocking order dated 05 August 2026.pdf"
    assert orders[-1].filename == "22_Blocking order dated 28 August 2026.pdf"
