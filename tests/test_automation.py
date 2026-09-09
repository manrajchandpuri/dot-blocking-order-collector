"""Release failures, rerun identity and Microsoft publication boundaries."""
import datetime as dt
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from dotbo import automation, sharepoint
from dotbo.catalog import Catalog, ListingPage


def result(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"source-pdf")
    archive = tmp_path / "orders.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(pdf, "1.pdf")
    docx = tmp_path / "orders.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<document/>")
    csv, html = tmp_path / "qa.csv", tmp_path / "qa.html"
    csv.write_text("qa")
    html.write_text("qa")
    order = NS(index=1, filename="1.pdf", pdf_path=str(pdf), order_date=dt.date(2026, 8, 1),
               urls=["example.com"], attachment=NS(att_id=1),
               letter=NS(action="block", subject="subject", case_no="case", court="court"))
    return NS(orders=[order], skipped=[], uncorroborated=[], zip_path=archive, docx_path=docx,
              report_csv=csv, report_html=html, total_urls=1,
              options=NS(year=2026, month=8, label="August 2026"))


def test_india_month_boundary_and_year_rollover():
    assert automation.target_month(now=dt.datetime(2026, 12, 31, 20, tzinfo=dt.timezone.utc)) == (2026, 12)
    assert automation.target_month(now=dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc)) == (2025, 12)
    for invalid in ("2026-13", "2026-8", "$(echo bad)", "0000-01"):
        with pytest.raises(ValueError):
            automation.target_month(invalid)


def test_release_gate_rejects_partial_and_corrupt_outputs(tmp_path):
    r = result(tmp_path)
    assert automation.release_issues(r) == []
    r.skipped = [(NS(att_id=99), "download failed")]
    r.uncorroborated = r.orders
    r.zip_path.write_bytes(b"broken")
    problems = automation.release_issues(r)
    assert any("99" in p for p in problems)
    assert any("corroborated" in p for p in problems)
    assert any("ZIP" in p for p in problems)
    r.orders = []
    assert any("No orders" in p for p in automation.release_issues(r))


def test_identity_ignores_output_metadata_but_detects_substantive_change(tmp_path):
    r = result(tmp_path)
    a = automation.make_manifest(r, "original")
    r.docx_path.write_bytes(b"different-container-timestamps")
    assert automation.make_manifest(r, "original")["package_key"] == a["package_key"]
    r.orders[0].urls.append("another.example")
    assert automation.make_manifest(r, "original")["package_key"] != a["package_key"]
    assert automation.make_manifest(r, "revision2")["package_key"] != a["package_key"]


class FakeGraph:
    root_id = "root"

    def __init__(self, fail_at=0, existing=False):
        self.uploaded = []
        self.fail_at, self.existing = fail_at, existing

    def folder(self, parent, name):
        return {"id": name, "webUrl": "https://tenant.sharepoint.com/" + name}

    def child(self, parent, name):
        return {"id": "existing", "webUrl": "https://tenant.sharepoint.com/marker"} if self.existing else None

    def upload(self, parent, name, path):
        if len(self.uploaded) + 1 == self.fail_at:
            raise RuntimeError("interrupted upload")
        self.uploaded.append((parent, name))
        return {"id": name, "webUrl": "https://tenant.sharepoint.com/" + name}


def test_marker_is_last_and_absent_on_partial_upload(tmp_path):
    manifest = automation.make_manifest(result(tmp_path), "original")
    broken = FakeGraph(fail_at=3)
    with pytest.raises(RuntimeError):
        sharepoint.publish(broken, manifest, tmp_path)
    assert not any(parent == "outbox" for parent, name in broken.uploaded)
    complete = FakeGraph()
    sharepoint.publish(complete, manifest, tmp_path)
    assert len(complete.uploaded) == 5
    assert complete.uploaded[-1][0] == "outbox"
    marker = json.loads((tmp_path / "published-package.json").read_text())
    assert all("web_url" in f for f in marker["files"].values())


def test_existing_marker_skips_all_uploads(tmp_path):
    manifest = automation.make_manifest(result(tmp_path), "original")
    client = FakeGraph(existing=True)
    assert sharepoint.publish(client, manifest, tmp_path)["id"] == "existing"
    assert client.uploaded == []


def test_modified_output_cannot_publish(tmp_path):
    r = result(tmp_path)
    manifest = automation.make_manifest(r, "original")
    r.docx_path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        sharepoint.publish(FakeGraph(), manifest, tmp_path)


def test_chunk_headers_and_final_size_check(tmp_path, monkeypatch):
    path = tmp_path / "large.zip"
    path.write_bytes(b"x" * (sharepoint.CHUNK + 10))
    calls = []
    client = sharepoint.GraphClient("drive", "root")
    monkeypatch.setattr(client, "call", lambda *a, **k: {"uploadUrl": "https://upload.example/session"})

    def request(method, url, **kwargs):
        calls.append(kwargs["headers"])
        final = len(calls) == 2
        return NS(status_code=201 if final else 202, json=lambda: {
            "size": path.stat().st_size, "id": "uploaded", "webUrl": "https://tenant.sharepoint.com/file"})
    monkeypatch.setattr(sharepoint, "checked_request", request)
    client.upload("root", "large.zip", path)
    assert len(calls) == 2
    assert all("Authorization" not in c for c in calls)
    assert calls[1]["Content-Range"] == f"bytes {sharepoint.CHUNK}-{path.stat().st_size - 1}/{path.stat().st_size}"


def test_transport_does_not_leak_signed_urls(monkeypatch):
    def fail(*a, **k):
        raise sharepoint.requests.ConnectionError("https://secret-upload-url")
    monkeypatch.setattr(sharepoint.requests, "request", fail)
    with pytest.raises(RuntimeError) as exc:
        sharepoint.checked_request("PUT", "https://secret-upload-url")
    assert "secret-upload" not in str(exc.value)


def test_strict_catalogue_does_not_hide_discovery_failure(monkeypatch):
    catalog = Catalog(strict=True)
    def fail():
        raise RuntimeError("source unavailable")
    monkeypatch.setattr(catalog, "_pages_by_search", fail)
    with pytest.raises(RuntimeError, match="source unavailable"):
        catalog.listing_pages(refresh=True)


def test_strict_catalogue_does_not_hide_attachment_failure(monkeypatch):
    catalog = Catalog(strict=True)
    monkeypatch.setattr(catalog, "attachment_ids", lambda _: [1])
    def fail(*a):
        raise RuntimeError("metadata unavailable")
    monkeypatch.setattr(catalog, "attachment", fail)
    with pytest.raises(RuntimeError, match="metadata unavailable"):
        catalog.page_attachments(ListingPage(1, "page"), refresh=True)


def test_preview_and_release_failure_never_upload(tmp_path, monkeypatch):
    r = result(tmp_path)
    monkeypatch.setattr(automation, "engine_status", lambda: ("tesseract", "ok"))
    monkeypatch.setattr(automation, "run", lambda *a, **k: r)
    def forbidden(*a, **k):
        pytest.fail("Microsoft must not be contacted")
    monkeypatch.setattr(sharepoint.GraphClient, "from_environment", forbidden)
    assert automation.main(["--month", "2026-08", "--output", str(tmp_path)]) == 0
    r.skipped = [(NS(att_id=1), "failed")]
    assert automation.main(["--month", "2026-08", "--output", str(tmp_path), "--publish"]) == 2
    assert json.loads((tmp_path / "run-summary.json").read_text())["status"] == "NeedsReview"
