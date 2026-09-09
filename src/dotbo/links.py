"""Reconstruct public dot.gov.in links from WordPress post ids.

The site's public URLs carry an obfuscated id suffix::

    https://www.dot.gov.in/documents/orders-and-notices/
      blocking-notifications-...-july-sept-2026-cTO2UzNtQWa

That suffix is ``base64("id-<post id>")`` with padding stripped and the whole
string reversed.  Verified against every listing page: 75697 -> cTO2UzNtQWa,
51032 -> IzMwETNtQWa, 51819 -> kTM4ETNtQWa, and the derived 51715 ->
UTM3ETNtQWa fetches with HTTP 200 and the matching childslug.

The API only ever gives us the CMS-internal URL, so without this the archive
could not link a reader back to the page an order actually lives on.
"""
from __future__ import annotations

import base64
import re
from urllib.parse import quote

SITE = "https://www.dot.gov.in"
DOCUMENTS_PATH = "/documents/orders-and-notices"


def id_hash(post_id: int) -> str:
    """The URL suffix the site uses for a post."""
    encoded = base64.b64encode(f"id-{int(post_id)}".encode()).decode()
    return encoded.rstrip("=")[::-1]


def parse_id_hash(suffix: str) -> int | None:
    """Recover the post id from a URL suffix, or None if it is not one."""
    padded = suffix[::-1]
    padded += "=" * (-len(padded) % 4)
    try:
        decoded = base64.b64decode(padded).decode()
    except Exception:  # noqa: BLE001 - any junk suffix simply is not an id
        return None
    m = re.fullmatch(r"id-(\d+)", decoded)
    return int(m.group(1)) if m else None


def page_url(post_id: int, slug: str, title: str = "") -> str:
    """The public page for a listing document."""
    slug = (slug or "").strip("/").rsplit("/", 1)[-1]
    url = f"{SITE}{DOCUMENTS_PATH}/{slug}-{id_hash(post_id)}"
    if title:
        # The site keeps "/" and "(" unescaped in pageTitle and joins words with
        # "-".  A run of spaces and dashes collapses to one: "(July - Sept 2026)"
        # becomes "(July-Sept-2026)", not "(July---Sept-2026)".
        pretty = re.sub(r"[\s\-‐‑‒–—]+", "-", title.strip())
        url += "?pageTitle=" + quote(pretty, safe="/-()%")
    return url


def slug_from_cms_url(url: str) -> str:
    """The trailing slug of a cms-dot.digifootprint.gov.in document URL."""
    return (url or "").rstrip("/").rsplit("/", 1)[-1]
