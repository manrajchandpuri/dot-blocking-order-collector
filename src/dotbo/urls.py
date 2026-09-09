"""URL / domain tokenisation, de-wrapping and noise filtering.

Everything here works on *text fragments* so it can be fed either by PyMuPDF's
text extraction or by OCR word output.  Two behaviours matter most:

* **De-wrapping.**  PDF line wrapping splits domains mid-token, e.g.
  ``hbytrcnlkss`` / ``x.xyz`` or ``tatatrentzudiofranchise.co`` / ``m``.
  We re-join a fragment with its neighbour when the join produces a valid
  domain and the pieces do not stand on their own.
* **Noise filtering.**  Blocking orders are full of URLs that are *not* being
  blocked: DoT/MeitY addresses, counsel email domains, court portals, and
  registrar company names sitting in a neighbouring table column.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from .config import DATA_DIR

# ---------------------------------------------------------------- TLD table


@functools.lru_cache(maxsize=1)
def tlds() -> frozenset[str]:
    path = DATA_DIR / "tlds.txt"
    out = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                out.add(line)
    except OSError:
        pass
    # Punycode TLDs arrive as xn--...; keep a couple of common extras.
    out.update({"onion", "local"})
    return frozenset(out)


# ---------------------------------------------------------------- patterns

_SCHEME = r"(?:https?|ftp)://"
_HOSTCHARS = r"[A-Za-z0-9¡-￿](?:[A-Za-z0-9¡-￿_-]*[A-Za-z0-9¡-￿])?"
_HOST = rf"(?:{_HOSTCHARS}\.)+[A-Za-z][A-Za-z0-9-]{{1,62}}"
_IPV4 = r"(?:\d{1,3}\.){3}\d{1,3}"
_PORT = r"(?::\d{2,5})?"
_PATH = r"(?:/[^\s,;<>\"'\)\]]*)?"

CANDIDATE_RE = re.compile(
    rf"(?:{_SCHEME})?(?:{_HOST}|{_IPV4}){_PORT}{_PATH}",
    re.UNICODE,
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# An email whose domain got cut by the line break: "...abbe14cc1b@p" / "rivacyguardian.org".
_OPEN_EMAIL_RE = re.compile(r"@[A-Za-z0-9.-]*$")

# Leading list markers: "1.", "1)", "(1)", "i.", ".", "-".  The trailing \s+ is
# load-bearing: without it "x.xyz" reads as the Roman numeral "x." plus "xyz".
_LEADER_RE = re.compile(r"^\s*(?:\(?\d{1,4}[.)\]]|\(?[ivxlcdm]{1,6}[.)]|[•\-–—*])\s+", re.I)

# Two-label hosts whose second-level part is one of these are legal-citation
# debris ("D. No. 6" -> "d.no", "I.A." -> "i.a"), never a blocked domain.
_ABBREV_SLD = {
    "no", "ia", "ors", "anr", "vs", "para", "pvt", "ltd", "sh", "ms", "mr",
    "adv", "hon", "col", "smt", "sc", "hc", "cs", "comm", "cm", "os", "wp",
}

# Second-level registry labels: "co.in" or "com.au" on their own is a public
# suffix, never a site anyone was ordered to block.
_PUBLIC_SUFFIX_SLD = {
    "co", "com", "net", "org", "gov", "edu", "ac", "mil", "int", "firm",
    "gen", "ind", "res", "nic", "or", "ne", "info", "biz", "sch",
}

# ---------------------------------------------------------------- denylists

DENY_SUFFIXES = (
    "gov.in", "nic.in", "gov.uk", "govcontractor.nic.in", "govcontractor.in",
    "digifootprint.gov.in", "sci.gov.in", "indiacode.nic.in",
)

DENY_HOSTS = {
    "about:blank", "about.blank", "dot.gov.in", "www.dot.gov.in",
    "meity.gov.in", "cyberlaw-legal.meity.gov.in", "delhihighcourt.nic.in",
    "example.com", "www.example.com", "gmail.com", "www.gmail.com",
    "nic.in", "gov.in", "hotmail.com", "yahoo.com", "outlook.com",
    "google.com", "www.google.com", "adobe.com", "www.w3.org", "w3.org",
    "schemas.openxmlformats.org", "purl.org", "creativecommons.org",
    "ns.adobe.com", "iptc.org", "whatsapp.com", "www.whatsapp.com",
}

# Registrar / intermediary corporate names that occupy the neighbouring column
# of the DoT annexure tables (May 21_ etc.).  Matched on the bare host.
# Corporate suffixes that sit in a registrar column and must never be glued to
# the domain on the following line ("LLC" + "asianpaintspartner.com").
_CORP_STEMS = {
    "llc", "inc", "ltd", "uab", "pvt", "corp", "co", "gmbh", "bv", "sa", "srl",
    "plc", "llp", "oy", "ab", "as", "no", "pte", "kft", "sarl", "ug", "spa",
}

DENY_REGISTRARS = {
    "godaddy.com", "www.godaddy.com", "namesilo.com", "namecheap.com",
    "hostinger.com", "publicdomainregistry.com", "dynadot.com", "tucows.com",
    "porkbun.com", "namebright.com", "cloudflare.com", "www.cloudflare.com",
    "google.com", "amazon.com", "aws.amazon.com", "registrar.amazon.com",
    "resellerclub.com", "bigrock.com", "hostgator.com", "wix.com",
    "squarespace.com", "shopify.com", "wordpress.com", "blogger.com",
    "facebook.com", "www.facebook.com", "twitter.com", "x.com",
    "instagram.com", "youtube.com", "www.youtube.com", "telegram.org",
    "onamae.com", "gname.com", "name.com", "similarweb.com", "whois.com",
    "drive.google.com", "docs.google.com", "dropbox.com", "wetransfer.com",
    "archive.org", "web.archive.org", "virustotal.com",
    # WHOIS-privacy and domain-marketplace hosts that fill the registrar column.
    "privacyguardian.org", "withheldforprivacy.com", "whoisguard.com",
    "domainsbyproxy.com", "perfectprivacy.com", "contactprivacy.com",
    "privacyprotect.org", "identity-protect.org", "anonymize.com",
    "sav.com", "sedo.com", "afternic.com", "dan.com", "hugedomains.com",
    "networksolutions.com", "enom.com", "epik.com", "internet.bs",
    "registrar-servers.com", "24xservice.com", "render.com",
    "domainshype.com", "gransy.com", "openprovider.com", "key-systems.net",
    "above.com", "dropcatch.com", "namebright.com", "snapnames.com",
    "netim.com", "nregistry.com", "gname.com", "dynadot.com", "namesilo.com",
    "west263.com", "ename.com", "regru.ru", "reg.ru", "incognet.io",
    "unstoppabledomains.com", "spaceship.com", "cloudyuqu.com", "csl-gmbh.net",
    "registrar.eu", "ascio.com", "pknic.net", "tldregistrarsolutions.com",
    "endurance.com", "hostingconcepts.nl", "1api.net", "csctwo.com",
    "realtimeregister.com", "cosmotown.com", "spaceship.com", "ionos.com",
}

# Boilerplate that appears in the DHC digital-signature footer / DoT annexure.
DENY_SUBSTRINGS = (
    "delhihighcourt", "ecourts", "digifootprint", "openxmlformats",
    "govcontractor", "@", "creativecommons", "adobe.com/",
)


@dataclass(frozen=True)
class Candidate:
    """A URL as it appeared, plus its comparison key."""

    text: str          # verbatim, e.g. "https://Vegamovies.taxi/ "-stripped
    key: str           # lowercase host+path, no scheme/www/trailing slash


# ---------------------------------------------------------------- helpers


def _host_of(token: str) -> str:
    t = re.sub(rf"^{_SCHEME}", "", token, flags=re.I)
    t = t.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return t.split(":", 1)[0].rstrip(".").lower()


def _tld_of(host: str) -> str:
    return host.rsplit(".", 1)[-1] if "." in host else ""


def is_valid_domain(token: str) -> bool:
    """True when the token's host ends in a real TLD (or is an IPv4 address)."""
    host = _host_of(token)
    if not host:
        return False
    if re.fullmatch(_IPV4, host):
        return all(0 <= int(p) <= 255 for p in host.split("."))
    if "." not in host:
        return False
    if any(len(lbl) == 0 for lbl in host.split(".")):
        return False
    return _tld_of(host) in tlds()


def longest_valid_prefix(token: str) -> str:
    """The longest leading part of ``token`` that is a real domain, or ''.

    Wide annexure tables sometimes run a URL straight into the registrar name
    with no separator - "https://ffff.onlineworldcupfifa.comCloudflare, Inc.".
    Only ever applied to a token that is *not* already valid, so a genuine
    ".company" domain is never clipped back to ".com".
    """
    best = ""
    for m in re.finditer(r"[./]", token):
        head = token[: m.start()]
        for end in range(m.start() + 1, len(token) + 1):
            candidate = token[:end]
            if is_valid_domain(candidate) and len(candidate) > len(best):
                best = candidate
        del head
    return best


def normalise_key(token: str) -> str:
    """Comparison key: scheme-less, www-less, lowercase, no trailing slash."""
    t = token.strip().strip(".,;:'\"()[]<>")
    t = re.sub(rf"^{_SCHEME}", "", t, flags=re.I)
    if t.lower().startswith("www."):
        t = t[4:]
    t = t.rstrip("/")
    return t.lower()


def is_noise(token: str) -> bool:
    host = _host_of(token)
    low = token.lower()
    if not host:
        return True
    bare = host[4:] if host.startswith("www.") else host
    if {host, bare} & (DENY_HOSTS | DENY_REGISTRARS):
        return True
    if any(host == s or host.endswith("." + s) for s in DENY_SUFFIXES):
        return True
    # A 32-hex-digit blob as the *second-level* label is a file hash or an email
    # local part left over from PDF metadata ("c22cf...327e.pr").  The same
    # string as a subdomain of a real host is an ordinary CDN name and stays.
    labels_all = bare.split(".")
    if len(labels_all) == 2 and re.fullmatch(r"[0-9a-f]{24,}", labels_all[0]):
        return True
    # A *bare* IP address is the resolved-IP column sitting beside the domain,
    # and neither reference compilation lists one.  An IP written with a scheme
    # ("http://103.165.93.31") is a target in its own right - order 82667 lists
    # nine of them - so that form is kept.
    if re.fullmatch(_IPV4, host) and not re.match(_SCHEME, token, re.I):
        return True
    if any(s in low for s in DENY_SUBSTRINGS):
        return True
    # A lone TLD-looking token such as "co.in" or a version string "1.2.3".
    if re.fullmatch(r"[\d.]+", host) and not re.fullmatch(_IPV4, host):
        return True
    # Legal-citation debris.  Only applied to bare "sld.tld" hosts so that real
    # entries with a short first label ("m.moviediskhd.shop") survive.
    labels = host.split(".")
    if len(labels) == 2 and not re.fullmatch(_IPV4, host):
        sld = labels[0]
        if len(sld) < 2 or sld in _ABBREV_SLD:
            return True
        if sld in _PUBLIC_SUFFIX_SLD and len(labels[1]) <= 3:
            return True
    # Prose picked up across a sentence boundary: "...rights. In such a case"
    # reads as "rights.In".  Every real entry in these annexures has a
    # lower-case TLD, so a capitalised one means we crossed a full stop.
    if not re.match(_SCHEME, token, re.I):
        raw_host = re.sub(r"^www\.", "", token.split("/", 1)[0], flags=re.I)
        raw_tld = raw_host.rsplit(".", 1)[-1].split(":", 1)[0]
        if any(ch.isupper() for ch in raw_tld):
            return True
    return False


# ---------------------------------------------------------------- de-wrapping


def _fragments(line: str) -> list[str]:
    """Split a line into whitespace/comma separated fragments, markers removed."""
    line = _LEADER_RE.sub("", line)
    line = line.replace("\u00ad", "")  # soft hyphen
    parts = re.split(r"[,;\s]+", line.strip())
    out = []
    for part in parts:
        part = part.strip("()[]{}<>\"'\u2018\u2019\u201c\u201d")
        part = part.strip()
        if part:
            out.append(part)
    return out


def _can_join(left: str, right: str) -> bool:
    """Should ``left`` and ``right`` be glued back into one domain?"""
    if not left or not right:
        return False
    if left.endswith(("/", "?", "&", "=")):
        return False
    # Only glue clean host-ish fragments; anything with stray punctuation is
    # prose, not a wrapped domain.  A scheme on the left is fine - that is the
    # commonest wrap of all ("https://live1.streambylivepulse.c" + "om").
    bare_left = re.sub(rf"^{_SCHEME}", "", left, flags=re.I)
    scheme_left = bool(re.match(_SCHEME, left, re.I))
    if not re.fullmatch(r"[A-Za-z0-9._~%+/-]+", bare_left) or not re.fullmatch(
        r"[A-Za-z0-9._~%+/-]+", right
    ):
        return False
    if left.lower().rstrip(".") in _CORP_STEMS:
        return False
    if left.endswith("."):
        # A trailing full stop is usually a sentence end - "etc." + "to",
        # "I.A." + "No", "Inc." + "asianpaintsdealer.com" - but it is also how a
        # TLD wraps ("watch.buffstreamsbackup." + "world").  Only a long final
        # label, or an explicit scheme, marks the second case.
        stem = bare_left[:-1]
        last_label = stem.rsplit(".", 1)[-1]
        if not scheme_left and len(last_label) < 4:
            return False
        if "." in right or not right.isalpha() or len(right) > 24:
            return False
    # An IP address is never the truncated head of a domain.
    if re.fullmatch(_IPV4, left):
        return False
    joined = left + right
    if not is_valid_domain(joined):
        return False
    if not is_valid_domain(left):
        # left is a bare wrap stem: "hbytrcnlkss" + "x.xyz", "Cdn-" + "port.com".
        # Short or purely numeric stems are page furniture ("(D. No. 6)"), not
        # wrapped domains - unless the stem ends in a hyphen, which is an
        # unambiguous wrap marker.
        if left.endswith("-"):
            return True
        if scheme_left:
            # A scheme'd token that is not a valid domain is unambiguously a
            # truncated URL, so its continuation is whatever comes next.
            return True
        if len(left) < 3 or left.isdigit() or not any(c.isalpha() for c in left):
            return False
        # When the right half already stands alone as a domain, gluing is only
        # justified by an unmistakable wrap: a long stem *and* a right half that
        # starts mid-label ("zudiofashionretailfranchis" + "e.com",
        # "https://popcornmovi" + "es.to").  Two consecutive list entries -
        # "1moviesonline" then "3kmovies.beauty" - fail the second test.
        if is_valid_domain(right):
            first_label = right.split(".", 1)[0]
            return len(left) >= 10 and len(first_label) <= 3
        return True
    if (
        not is_valid_domain(right)
        and len(right) <= 3
        and "." not in right
        and right.isalpha()
    ):
        # left is complete but truncated: "...franchise.co" + "m"
        return True
    return False


def dewrap(fragments: list[str]) -> list[str]:
    """Glue fragments split by PDF line/word wrapping.

    A token that stays invalid after joining may instead be a URL run into the
    next column with no separator, so the last step clips it back to its longest
    valid prefix.
    """
    out: list[str] = []
    i = 0
    while i < len(fragments):
        cur = fragments[i]
        while i + 1 < len(fragments) and _can_join(cur, fragments[i + 1]):
            cur = cur + fragments[i + 1]
            i += 1
        if re.match(_SCHEME, cur, re.I) and not is_valid_domain(cur):
            cur = longest_valid_prefix(cur) or cur
        out.append(cur)
        i += 1
    return out


# ---------------------------------------------------------------- public API


def candidates_from_lines(
    lines: list[str], *, allow_email_domains: bool = False, dedupe: bool = True
) -> list[Candidate]:
    """Extract blocked-URL candidates from a block of text lines, in order.

    Fragments are de-wrapped across line boundaries, so a domain broken by the
    PDF's line wrapping is recovered.

    With ``dedupe=False`` every occurrence is returned.  That is how DoT counts:
    the letter for order 82667 declares 586 websites, and the annexure holds 586
    rows covering 575 distinct URLs.
    """
    frags: list[str] = []
    drop_next = False
    for line in lines:
        if not allow_email_domains:
            line = EMAIL_RE.sub(" ", line)
        parts = _fragments(line)
        if not allow_email_domains and line.lstrip().startswith("@") and parts:
            # This line *starts* an email domain whose local part wrapped onto
            # the line above ("...abbe14cc1b" / "@privacyguardian.org").
            parts = parts[1:]
        if drop_next and parts:
            # This line continues an email address broken by the PDF's wrapping,
            # so its first fragment is the tail of that address, not a domain.
            parts = parts[1:]
        drop_next = bool(not allow_email_domains and _OPEN_EMAIL_RE.search(line.rstrip()))
        frags.extend(parts)
    joined = dewrap(frags)

    out: list[Candidate] = []
    seen: set[str] = set()
    for frag in joined:
        for m in CANDIDATE_RE.finditer(frag):
            token = m.group(0).strip().strip(".,;:'\"()[]<>")
            if not token or not is_valid_domain(token) or is_noise(token):
                continue
            key = normalise_key(token)
            if not key:
                continue
            if dedupe:
                if key in seen:
                    continue
                seen.add(key)
            out.append(Candidate(text=token, key=key))
    return out


def candidates_from_text(text: str, **kw) -> list[Candidate]:
    return candidates_from_lines(text.splitlines(), **kw)


MAX_FRAGMENT_LABEL = 4


def drop_fragments(cands: list[Candidate]) -> list[Candidate]:
    """Remove left-truncated leftovers of a domain that wrapped mid-token.

    Wide annexure tables interleave columns, so the two halves of a wrapped
    domain can end up separated by an IP address and never get glued back
    together - leaving ``es.to`` beside the real ``popcornmovies.to``.  A short
    first label that is also the tail of a longer entry is that leftover.
    """
    keys = [c.key for c in cands]
    out: list[Candidate] = []
    for c in cands:
        # The same site listed twice in adjacent columns - "af4k.xyz" beside
        # "https://af4k.xyz:8443".  Keep the fuller form.
        if any(
            other != c.key and other.startswith(c.key) and other[len(c.key)] in ":/"
            for other in keys
        ):
            continue
        first = c.key.split(".", 1)[0]
        if len(first) <= MAX_FRAGMENT_LABEL and not re.match(_SCHEME, c.text, re.I):
            fragment = False
            for other in keys:
                if other == c.key or not other.endswith(c.key):
                    continue
                prefix = other[: -len(c.key)]
                if prefix and (prefix[-1].isalnum() or prefix[-1] == "-"):
                    fragment = True
                    break
            if fragment:
                continue
        out.append(c)
    return out


def looks_like_target(text: str) -> bool:
    """Does this line hold something a list entry would point at?

    Deliberately laxer than :func:`candidates_from_lines` - it accepts hosts we
    would filter out, such as the bare IP that is entry 115 of order 83543 -
    because the question here is "is this a list entry?", not "is this a URL we
    report?".
    """
    for m in CANDIDATE_RE.finditer(text or ""):
        if is_valid_domain(m.group(0).strip().strip(".,;:'\"()[]<>")):
            return True
    return False


def dedupe(cands: list[Candidate]) -> list[Candidate]:
    out, seen = [], set()
    for c in cands:
        if c.key in seen:
            continue
        seen.add(c.key)
        out.append(c)
    return out
