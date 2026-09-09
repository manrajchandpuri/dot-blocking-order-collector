"""Score the extraction engine against the hand-built May 2025 reference.

This runs offline against the PDFs in the reference folder, so it measures the
extraction engine alone - no catalogue, no downloads.
"""
import os
from pathlib import Path

import pytest

from eval_may import REF, main

pytestmark = pytest.mark.skipif(
    not Path(REF).is_dir() or os.environ.get("DOTBO_SKIP_SLOW") == "1",
    reason="reference folder unavailable, or slow tests disabled",
)

MIN_PRECISION = 0.99
MIN_RECALL = 0.98


def test_may_2025_precision_and_recall():
    precision, recall = main(use_ocr=True)
    assert precision >= MIN_PRECISION, f"precision {precision:.4f} below {MIN_PRECISION}"
    assert recall >= MIN_RECALL, f"recall {recall:.4f} below {MIN_RECALL}"
