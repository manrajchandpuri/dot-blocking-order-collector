"""QA report: what was extracted, and where it needs a human eye.

The run is fully automatic, so this is where anything doubtful surfaces - a
count that disagrees with the DoT letter, a list found in the court order rather
than an annexure, a page that had to be OCR'd, or an order that failed outright.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
from pathlib import Path

from .naming import Order, compilation_name

COLUMNS = [
    "no", "filename", "order_date", "date_source", "case_no", "court", "action",
    "urls_found", "declared_count", "count_match", "corroborated",
    "corroboration", "confidence", "source", "pages", "strategy", "merged_from",
    "ocr_pages", "ocr_engine", "attachment_id", "pdf_url", "notes",
]


def rows(orders: list[Order]) -> list[dict]:
    out = []
    for o in orders:
        ex, letter = o.extraction, o.letter
        match = "" if ex is None or ex.count_matches is None else ("yes" if ex.count_matches else "NO")
        out.append(
            {
                "no": o.index,
                "filename": o.filename,
                "order_date": o.order_date.isoformat() if o.order_date else "",
                "date_source": o.date_source,
                "case_no": letter.case_no if letter else "",
                "court": letter.court if letter else "",
                "action": letter.action if letter else "",
                "urls_found": ex.count if ex else 0,
                "declared_count": "" if not ex or ex.declared_count is None else ex.declared_count,
                "count_match": match,
                "corroborated": "yes" if ex and ex.corroborated else "no",
                "corroboration": ex.corroboration if ex else "",
                "confidence": ex.confidence if ex else "low",
                "source": ex.source if ex else "none",
                "pages": " ".join(str(p) for p in ex.pages) if ex else "",
                "strategy": ex.strategy if ex else "",
                "merged_from": " ".join(ex.merged_from) if ex else "",
                "ocr_pages": " ".join(str(p) for p in ex.ocr_pages) if ex else "",
                "ocr_engine": ex.ocr_engine if ex else "",
                "attachment_id": o.attachment.att_id,
                "pdf_url": o.attachment.pdf_url,
                "notes": " | ".join(([o.error] if o.error else []) + (ex.notes if ex else [])),
            }
        )
    return out


def uncorroborated(order: Order) -> bool:
    """True only when the engine has no independent support for its answer.

    Deliberately *not* triggered by OCR having been used, or by the list having
    come from the court order rather than an annexure.  Those are facts about
    the source document, recorded in the columns above; they say nothing about
    whether the extraction is right.  What matters is whether something other
    than the extraction itself confirms the result - the count the DoT letter
    declares, the annexure's own numbering, or two readings agreeing.
    """
    ex = order.extraction
    if order.error or ex is None:
        return True
    if not ex.urls:
        return True
    return not ex.corroborated


# Kept under the old name for callers that only want the report's row shading.
needs_review = uncorroborated


def write_csv(orders: list[Order], path: str | Path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows(orders))
    return path


def write_html(orders: list[Order], year: int, month: int, path: str | Path) -> Path:
    path = Path(path)
    data = rows(orders)
    flagged = [r for r, o in zip(data, orders) if uncorroborated(o)]
    total_urls = sum(r["urls_found"] for r in data)

    def cell(value, klass=""):
        return f'<td class="{klass}">{html.escape(str(value))}</td>'

    body = []
    for row, order in zip(data, orders):
        klass = "flag" if uncorroborated(order) else ""
        body.append(
            "<tr class='%s'>" % klass
            + cell(row["no"])
            + cell(row["filename"])
            + cell(row["order_date"])
            + cell(row["case_no"])
            + cell(row["action"])
            + cell(row["urls_found"])
            + cell(row["declared_count"])
            + cell(row["count_match"])
            + cell(row["corroboration"] or ("-" if row["corroborated"] == "no" else ""))
            + cell(row["source"])
            + cell(row["ocr_engine"])
            + cell(row["notes"])
            + "</tr>"
        )

    title = compilation_name(year, month)
    path.write_text(
        f"""<!doctype html><meta charset="utf-8"><title>QA report - {html.escape(title)}</title>
<style>
 body{{font:13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:2rem;color:#1a1a1a}}
 h1{{font-size:1.3rem;margin:0 0 .25rem}}
 p.meta{{color:#666;margin:0 0 1.5rem}}
 table{{border-collapse:collapse;width:100%;font-size:12px}}
 th,td{{border:1px solid #ddd;padding:5px 7px;text-align:left;vertical-align:top}}
 th{{background:#f4f4f5;position:sticky;top:0}}
 tr.flag{{background:#fff7e6}}
 tr.flag td:first-child{{border-left:3px solid #e08c00}}
 .summary{{display:flex;gap:2rem;margin-bottom:1.5rem}}
 .summary div{{background:#f4f4f5;padding:.75rem 1rem;border-radius:6px}}
 .summary b{{display:block;font-size:1.4rem}}
</style>
<h1>QA report - {html.escape(title)}</h1>
<p class="meta">Generated {dt.datetime.now():%d %B %Y, %H:%M}</p>
<div class="summary">
  <div><b>{len(data)}</b>orders</div>
  <div><b>{total_urls}</b>URLs extracted</div>
  <div><b>{len(data) - len(flagged)}/{len(data)}</b>independently corroborated</div>
</div>
<table><thead><tr>
<th>#</th><th>File</th><th>Date</th><th>Case</th><th>Action</th><th>URLs</th>
<th>Declared</th><th>Match</th><th>Corroborated by</th><th>Source</th><th>OCR</th><th>Notes</th>
</tr></thead><tbody>{''.join(body)}</tbody></table>
""",
        encoding="utf-8",
    )
    return path
