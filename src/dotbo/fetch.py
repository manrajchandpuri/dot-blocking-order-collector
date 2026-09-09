"""Download and cache the order PDFs."""
from __future__ import annotations

import concurrent.futures
from pathlib import Path

from .catalog import Attachment
from .config import PDF_CACHE, ensure_dirs
from .http import PoliteSession, shared

CHUNK = 1 << 16


def cached_path(att: Attachment) -> Path:
    return PDF_CACHE / f"{att.att_id}.pdf"


def is_cached(att: Attachment) -> bool:
    path = cached_path(att)
    if not path.is_file():
        return False
    if att.filesize and path.stat().st_size != att.filesize:
        return False
    return path.stat().st_size > 0


def download(att: Attachment, session: PoliteSession | None = None, force: bool = False) -> Path:
    ensure_dirs()
    path = cached_path(att)
    if not force and is_cached(att):
        return path
    if not att.pdf_url:
        raise RuntimeError(f"attachment {att.att_id} has no PDF url")
    session = session or shared()
    tmp = path.with_suffix(".part")
    with session.stream(att.pdf_url) as resp:
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(CHUNK):
                if chunk:
                    fh.write(chunk)
    tmp.replace(path)
    return path


def download_all(
    attachments: list[Attachment],
    *,
    force: bool = False,
    max_workers: int = 4,
    progress=None,
) -> dict[int, Path | Exception]:
    """Fetch every PDF, returning a path or the exception that stopped it."""
    ensure_dirs()
    session = shared()
    out: dict[int, Path | Exception] = {}
    total = len(attachments)

    def one(att: Attachment) -> Path | Exception:
        try:
            return download(att, session, force=force)
        except Exception as exc:  # noqa: BLE001 - reported per order, run continues
            return exc

    with concurrent.futures.ThreadPoolExecutor(max_workers) as pool:
        futures = {pool.submit(one, att): att for att in attachments}
        # Consume results on the calling thread: `progress` may touch a UI that
        # is not safe to call from a worker (Streamlit raises NoSessionContext).
        for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            att = futures[future]
            out[att.att_id] = future.result()
            if progress:
                progress(done, total, att)
    return out
