"""Unattended build with a release gate. Run: python -m dotbo.automation."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import RunOptions
from .ocr import engine_status
from .pipeline import run


def target_month(value: str = "", now: dt.datetime | None = None) -> tuple[int, int]:
    if value:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError("Month must be YYYY-MM")
        year, month = map(int, value.split("-"))
        dt.date(year, month, 1)
        return year, month
    today = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo("Asia/Kolkata")).date()
    previous = today.replace(day=1) - dt.timedelta(days=1)
    return previous.year, previous.month


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def release_issues(result) -> list[str]:
    issues = [f"Attachment {a.att_id}: {reason}" for a, reason in result.skipped]
    if not result.orders:
        issues.append("No orders found: investigate before declaring a zero-order month")
    issues.extend(f"Order {o.index}: extraction is not corroborated" for o in result.uncorroborated)
    expected = [result.zip_path, result.docx_path, result.report_csv, result.report_html]
    for path in expected:
        if not path or not Path(path).is_file() or not Path(path).stat().st_size:
            issues.append(f"Required output is missing or empty: {path}")
    if result.zip_path and Path(result.zip_path).is_file():
        try:
            with zipfile.ZipFile(result.zip_path) as archive:
                if archive.testzip() or sorted(archive.namelist()) != sorted(o.filename for o in result.orders):
                    issues.append("ZIP contents do not match the discovered orders")
        except zipfile.BadZipFile:
            issues.append("ZIP is invalid")
    if result.docx_path and Path(result.docx_path).is_file():
        try:
            with zipfile.ZipFile(result.docx_path) as archive:
                if archive.testzip() or "word/document.xml" not in archive.namelist():
                    issues.append("Word document is invalid")
        except zipfile.BadZipFile:
            issues.append("Word document is invalid")
    return issues


def make_manifest(result, revision: str) -> dict:
    # Output ZIP/DOCX timestamps change on a rebuild. Hash source bytes and
    # substantive extracted content instead, so identical reruns deduplicate.
    orders = [{
        "attachment_id": o.attachment.att_id, "filename": o.filename,
        "pdf_sha256": sha256(Path(o.pdf_path)), "date": o.order_date.isoformat(),
        "urls": o.urls, "action": o.letter.action, "subject": o.letter.subject,
        "case_no": o.letter.case_no, "court": o.letter.court,
    } for o in result.orders]
    period = f"{result.options.year:04d}-{result.options.month:02d}"
    fingerprint = hashlib.sha256(json.dumps(
        {"period": period, "orders": orders, "revision": revision},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    roles = {"zip": result.zip_path, "docx": result.docx_path,
             "qa_csv": result.report_csv, "qa_html": result.report_html}
    return {
        "schema_version": 1, "status": "Ready", "period": period,
        "label": result.options.label, "package_key": f"{period}-{fingerprint}",
        "revision": revision, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "order_count": len(orders), "url_count": result.total_urls,
        "run_url": (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
                    f"/actions/runs/{os.environ['GITHUB_RUN_ID']}") if os.getenv("GITHUB_RUN_ID") else "",
        "commit": os.getenv("GITHUB_SHA", "local"),
        "files": {role: {"name": Path(path).name, "size": Path(path).stat().st_size,
                          "sha256": sha256(Path(path))} for role, path in roles.items()},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default="", help="YYYY-MM; default previous month in India")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--publish", action="store_true", help="Upload a passing package to SharePoint")
    parser.add_argument("--revision", default="original", help="Change only for a deliberate reissue")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"status": "Failed", "issues": []}
    try:
        year, month = target_month(args.month)
        summary["period"] = f"{year:04d}-{month:02d}"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args.revision):
            raise ValueError("Revision must be 1-40 letters, digits, hyphens or underscores")
        engine, _ = engine_status()
        if engine != "tesseract":
            raise RuntimeError("Unattended builds require a working Tesseract installation")
        result = run(RunOptions(
            year=year, month=month, output_dir=args.output, refresh_index=True,
            redownload=True, max_workers=2, strict_collection=True,
        ), on_progress=lambda event: print(str(event), flush=True))
        issues = release_issues(result)
        summary.update(order_count=len(result.orders), url_count=result.total_urls, issues=issues)
        if issues:
            summary["status"] = "NeedsReview"
            return 2
        manifest = make_manifest(result, args.revision)
        (args.output / "package.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        summary.update(status="Preview", package_key=manifest["package_key"])
        if args.publish:
            from .sharepoint import GraphClient, publish
            published = publish(GraphClient.from_environment(), manifest, args.output)
            summary.update(status="Published", marker_url=published["webUrl"])
        return 0
    except Exception as exc:
        # Graph transport errors are sanitized by the integration layer.
        summary.update(status="Failed", issues=[str(exc)])
        print(f"Automation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        (args.output / "run-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
