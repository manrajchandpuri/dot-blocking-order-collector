"""A deliberately polite HTTP session for dot.gov.in.

The site's WAF blocks the whole client IP - not just the endpoint - after a
burst of requests, and stays blocked for a while.  Every request in this
project therefore goes through one shared, rate-limited session with backoff on
403/429 rather than straight through ``requests``.
"""
from __future__ import annotations

import threading
import time

import requests

from .config import USER_AGENT

MIN_INTERVAL = 0.35        # seconds between requests, process-wide
MAX_TRIES = 4
BACKOFF_BASE = 4.0         # seconds; doubles per retry on a throttling status
THROTTLED = (403, 429, 503)


class Throttled(RuntimeError):
    """The site is rate-limiting us; retrying immediately will not help."""


class PoliteSession:
    def __init__(self, min_interval: float = MIN_INTERVAL):
        self.min_interval = min_interval
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            }
        )
        self._lock = threading.Lock()
        self._next_at = 0.0

    def _wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_at:
                time.sleep(self._next_at - now)
                now = time.monotonic()
            self._next_at = now + self.min_interval

    def get(self, url: str, params: dict | None = None, **kw) -> requests.Response:
        last: Exception | None = None
        for attempt in range(MAX_TRIES):
            self._wait()
            try:
                resp = self._session.get(url, params=params, timeout=kw.pop("timeout", 60), **kw)
            except requests.RequestException as exc:
                last = exc
                time.sleep(BACKOFF_BASE * (attempt + 1))
                continue
            if resp.status_code in THROTTLED:
                last = Throttled(f"{resp.status_code} from {url}")
                time.sleep(BACKOFF_BASE * (2**attempt))
                continue
            resp.raise_for_status()
            return resp
        raise last or RuntimeError(f"GET {url} failed")

    def json(self, url: str, params: dict | None = None):
        return self.get(url, params).json()

    def stream(self, url: str):
        return self.get(url, stream=True)


_shared: PoliteSession | None = None
_shared_lock = threading.Lock()


def shared() -> PoliteSession:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = PoliteSession()
    return _shared
