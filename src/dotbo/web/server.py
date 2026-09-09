"""The local web front end: a small Starlette app, no framework beyond it.

Builds run on a worker thread and publish :class:`dotbo.progress.Progress`
events onto a queue, which the browser consumes over Server-Sent Events.  That
reuses the staged progress the pipeline already emits rather than inventing a
second notion of "where are we".
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import backfill as backfill_mod
from .. import library, progress as prog, report
from ..config import MONTHS, RunOptions
from ..naming import compilation_name
from ..ocr import engine_status
from ..pipeline import run as run_pipeline
from ..search import Index

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
TEMPLATES = HERE / "templates"


# ---------------------------------------------------------------- run state


@dataclass
class Job:
    """One build, and everything the browser needs to follow it."""

    job_id: str
    label: str
    kind: str = "month"                 # "month" | "backfill"
    events: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str = ""
    done: bool = False
    files: dict[str, Path] = field(default_factory=dict)
    _waiters: list[asyncio.Queue] = field(default_factory=list)
    _loop: asyncio.AbstractEventLoop | None = None
    backfill: backfill_mod.Backfill | None = None

    def publish(self, payload: dict) -> None:
        self.events.append(payload)
        loop = self._loop
        if loop is None:
            return
        for queue in list(self._waiters):
            loop.call_soon_threadsafe(queue.put_nowait, payload)


JOBS: dict[str, Job] = {}
INDEX = Index()
_INDEX_LOCK = threading.Lock()


def refresh_index() -> None:
    with _INDEX_LOCK:
        INDEX.reload()


# ---------------------------------------------------------------- pages


async def home(request):
    html = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


async def bootstrap(request):
    """Everything the page needs on load."""
    engine, detail = engine_status()
    return JSONResponse(
        {
            "months": MONTHS,
            "ocr": {"engine": engine, "detail": detail},
            "library": _library_payload(),
            "pending_months": len(backfill_mod.pending()),
            "output_dir": str(Path.home() / "Downloads"),
            "catalogued": len(INDEX.records),
            "indexed_urls": sum(len(r.urls) for r in INDEX.records),
        }
    )


def _library_payload() -> list[dict]:
    return [
        {
            "slug": m.slug,
            "label": m.label,
            "year": m.year,
            "month": m.month,
            "orders": m.order_count,
            "urls": m.url_count,
            "verified": m.corroborated_count,
            "built_on": m.built_on,
            "has_docx": m.docx_path.is_file(),
        }
        for m in library.entries()
    ]


# ---------------------------------------------------------------- building


async def start_run(request):
    body = await request.json()
    try:
        year, month = int(body["year"]), int(body["month"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "year and month are required"}, status_code=400)

    days = tuple(sorted({int(d) for d in body.get("days") or []}))
    options = RunOptions(
        year=year,
        month=month,
        days=days,
        look_ahead_days=int(body.get("look_ahead_days") or 31),
        use_ocr=bool(body.get("use_ocr", True)),
        tesseract_path=str(body.get("tesseract_path") or ""),
        refresh_index=bool(body.get("refresh_index", False)),
        redownload=bool(body.get("redownload", False)),
        output_dir=Path(body.get("output_dir") or (Path.home() / "Downloads")).expanduser(),
    )

    job = Job(job_id=uuid.uuid4().hex[:12], label=compilation_name(year, month))
    job._loop = asyncio.get_running_loop()
    JOBS[job.job_id] = job
    threading.Thread(target=_execute_run, args=(job, options), daemon=True).start()
    return JSONResponse({"job": job.job_id, "label": job.label})


def _execute_run(job: Job, options: RunOptions) -> None:
    def on_progress(event: prog.Progress) -> None:
        job.publish(
            {
                "type": "progress",
                "stage": event.stage,
                "label": event.label,
                "detail": event.detail,
                "message": event.message,
                "fraction": round(event.fraction, 4),
                "current": event.current,
                "total": event.total,
            }
        )

    def on_order(order) -> None:
        ex = order.extraction
        row = {
            "index": order.index,
            "filename": order.filename,
            "urls": len(order.urls),
            "declared": ex.declared_count if ex else None,
            "verified": bool(ex and ex.corroborated),
            "note": (ex.corroboration if ex else "") or (order.error or ""),
        }
        job.orders.append(row)
        job.publish({"type": "order", "order": row})

    try:
        result = run_pipeline(options, on_progress=on_progress, on_order=on_order)
        job.files = {
            "zip": result.zip_path,
            "docx": result.docx_path,
            "report_html": result.report_html,
            "report_csv": result.report_csv,
        }
        job.result = {
            "label": job.label,
            "orders": len(result.orders),
            "urls": result.total_urls,
            "verified": result.corroborated_count,
            "output_dir": str(options.output_dir),
            "rows": report.rows(result.orders),
            "files": [k for k, v in job.files.items() if v and Path(v).is_file()],
            "slug": library.slug_for(options.year, options.month),
        }
        refresh_index()
    except Exception as exc:  # noqa: BLE001 - reported to the page, not the console
        job.error = f"{type(exc).__name__}: {exc}"
        job.publish({"type": "error", "message": job.error, "trace": traceback.format_exc()})
    finally:
        job.done = True
        job.publish({"type": "done", "result": job.result, "error": job.error})


async def start_backfill(request):
    body = await request.json() if await request.body() else {}
    job = Job(job_id=uuid.uuid4().hex[:12], label="Back-catalogue", kind="backfill")
    job._loop = asyncio.get_running_loop()
    job.backfill = backfill_mod.Backfill(
        use_ocr=bool(body.get("use_ocr", True)),
        tesseract_path=str(body.get("tesseract_path") or ""),
    )
    JOBS[job.job_id] = job
    threading.Thread(target=_execute_backfill, args=(job,), daemon=True).start()
    return JSONResponse({"job": job.job_id, "label": job.label})


def _execute_backfill(job: Job) -> None:
    task = job.backfill
    try:
        def say(message: str) -> None:
            total = max(task.total, 1)
            complete = len(task.done) + len(task.failed)
            job.publish(
                {
                    "type": "progress",
                    "stage": "extract",
                    "label": task.current or "Back-catalogue",
                    "detail": f"{complete}/{task.total} months" if task.total else "Preparing",
                    "message": message,
                    "fraction": round(complete / total, 4),
                    "current": complete,
                    "total": task.total,
                }
            )

        task.execute(on_progress=say, on_month=lambda _t: refresh_index())
        job.result = {
            "label": "Back-catalogue",
            "built": task.done,
            "failed": task.failed,
            "stopped": task.stopping,
        }
        refresh_index()
    except Exception as exc:  # noqa: BLE001
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.done = True
        job.publish({"type": "done", "result": job.result, "error": job.error})


async def stop_job(request):
    job = JOBS.get(request.path_params["job_id"])
    if job and job.backfill:
        job.backfill.stop()
        return JSONResponse({"stopping": True})
    return JSONResponse({"stopping": False}, status_code=404)


async def job_state(request):
    job = JOBS.get(request.path_params["job_id"])
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return JSONResponse(
        {"done": job.done, "error": job.error, "result": job.result, "orders": job.orders}
    )


async def job_stream(request):
    job = JOBS.get(request.path_params["job_id"])
    if job is None:
        return PlainTextResponse("unknown job", status_code=404)
    queue: asyncio.Queue = asyncio.Queue()
    job._waiters.append(queue)
    backlog = list(job.events)

    async def events():
        for payload in backlog:
            yield f"data: {json.dumps(payload)}\n\n"
        if job.done:
            return
        try:
            while True:
                payload = await queue.get()
                yield f"data: {json.dumps(payload)}\n\n"
                if payload.get("type") == "done":
                    return
        finally:
            if queue in job._waiters:
                job._waiters.remove(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- files


async def download_job_file(request):
    job = JOBS.get(request.path_params["job_id"])
    kind = request.path_params["kind"]
    if job is None or kind not in job.files:
        return PlainTextResponse("not found", status_code=404)
    path = job.files[kind]
    if not path or not Path(path).is_file():
        return PlainTextResponse("not found", status_code=404)
    return _send(Path(path))


async def download_library_file(request):
    slug, kind = request.path_params["slug"], request.path_params["kind"]
    month = library.load(slug)
    if month is None:
        return PlainTextResponse("not found", status_code=404)
    if kind == "zip":
        target = month.directory / f"{month.label}.zip"
        try:
            library.rebuild_zip(slug, target)
        except Exception as exc:  # noqa: BLE001
            return PlainTextResponse(f"could not rebuild: {exc}", status_code=500)
        # The ZIP is a derivative of the PDF cache; do not keep it around.
        return _send(target, cleanup=target)
    paths = {"docx": month.docx_path, "report_html": month.report_html, "report_csv": month.report_csv}
    path = paths.get(kind)
    if path is None or not path.is_file():
        return PlainTextResponse("not found", status_code=404)
    return _send(path)


def _send(path: Path, cleanup: Path | None = None) -> FileResponse:
    media, _ = mimetypes.guess_type(path.name)
    task = BackgroundTask(_unlink, cleanup) if cleanup else None
    return FileResponse(
        path,
        media_type=media or "application/octet-stream",
        filename=path.name,
        background=task,
    )


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def library_list(request):
    return JSONResponse({"months": _library_payload()})


# ---------------------------------------------------------------- search


async def search(request):
    query = request.query_params.get("q", "").strip()
    if not query:
        return JSONResponse({"query": "", "hits": [], "total": 0})
    with _INDEX_LOCK:
        hits = INDEX.search(query, limit=200)
        total_records = len(INDEX.records)
    return JSONResponse(
        {
            "query": query,
            "total": len(hits),
            "indexed": total_records,
            "hits": [
                {
                    "attachment_id": h.record.attachment_id,
                    "tier": h.record.tier,
                    "index": h.record.index,
                    "title": h.record.title,
                    "case": h.record.case_no,
                    "court": h.record.court,
                    "action": h.record.action,
                    "date": h.record.date,
                    "urls": len(h.record.urls),
                    "matched": h.matched_urls,
                    "month_slug": h.record.month_slug,
                    "month_label": h.record.month_label,
                    "pdf_url": h.record.pdf_url,
                    "public_url": h.record.public_url,
                    "listing_title": h.record.listing_title,
                    "listing_url": h.record.listing_url,
                }
                for h in hits
            ],
        }
    )


# ---------------------------------------------------------------- app

routes = [
    Route("/", home),
    Route("/api/bootstrap", bootstrap),
    Route("/api/run", start_run, methods=["POST"]),
    Route("/api/backfill", start_backfill, methods=["POST"]),
    Route("/api/job/{job_id}/stop", stop_job, methods=["POST"]),
    Route("/api/job/{job_id}", job_state),
    Route("/api/job/{job_id}/stream", job_stream),
    Route("/api/job/{job_id}/file/{kind}", download_job_file),
    Route("/api/library", library_list),
    Route("/api/library/{slug}/{kind}", download_library_file),
    Route("/api/search", search),
    Mount("/static", app=StaticFiles(directory=str(STATIC)), name="static"),
]

app = Starlette(routes=routes)
