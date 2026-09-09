# DoT Blocking Order Collector

**Monthly firm deployment:** start with [the Microsoft + GitHub deployment guide](deployment/DEPLOYMENT-GUIDE.md).
It includes private repository upload, Microsoft identity setup, SharePoint,
Power Automate delivery and missing-run monitoring. Scheduling stays disabled
until configured. Build the clean upload ZIP with `python scripts/package_deployment.py`.

Builds a month's compilation of the Department of Telecommunications' blocking
instructions to Internet Service Licensees: a ZIP of correctly named PDFs and a
single Word document listing every URL those orders direct to be blocked. Keeps
every month you build, and lets you search across all of them.

Double-click **`Run DoT Collector.command`**. It starts the app and opens it in
your browser; leave the Terminal window it opens alone while you work, and close
it when you are done. On a fresh machine the first run sets up a virtual
environment and installs the dependencies, which takes a couple of minutes.

| File | What it is |
| --- | --- |
| `Blocking Orders_<Month> <Year>.zip` | The order PDFs, named `1_Blocking order dated 05 August 2026.pdf`, … |
| `Blocking Orders_<Month> <Year>.docx` | Every order's blocked URLs, in the reference format |
| `… - QA report.html` / `.csv` | Per-order audit trail: what was found, and what confirmed it |

## The three panels

**Build** — pick a month and year, optionally specific dates, and watch it work
through five stages with a live table of each order as it lands. Files are
written to the folder you choose and are also downloadable from the page.

**Archive** — every month you have built, re-downloadable. Also the
back-catalogue job, which builds every month DoT has published, oldest first,
skipping anything already there. It can be stopped at any point and resumed;
what is finished stays finished.

**Search** — see below.

## How it decides what to extract

A blocking order PDF is a bundle: the DoT covering letter, the annexure listing
the websites, forwarded emails, the court order, plaintiff affidavits, and
evidence screenshots. Only one part is the list.

**The covering letter on page 1 is the authority.** It states the order's own
date, whether the action is blocking or unblocking, how many websites are
involved (`[ 37 nos.]`, `[2+9=11 nos]`, `[402 Nos]`, `the 586 domains/URLs`),
and where the list lives. Reading the letter first is what stops the engine
listing the fifteen Facebook URLs in one May 2025 order's enclosed court
annexure when only `https://backpotin.com` was actually to be blocked.

Extraction is then a **constrained search**: candidate page spans are read with
several strategies (plain text, column bands, table cells, a single table
column), and the set whose size agrees with the letter's declared count wins.
Where nothing agrees exactly, the readings of the best span are merged rather
than one being picked — a blocked URL missing from the compilation is worse than
a stray one.

Every result is then **corroborated against something other than itself**:

1. the count the DoT letter declares, on distinct URLs or on annexure rows
   (a list can name the same site twice — order 82667 declares 586 rows covering
   575 distinct URLs);
2. the annexure's own numbering;
3. two different reading strategies arriving at the same set of URLs, which
   covers orders whose letter declares no count at all.

The run does not ask you to adjudicate anything. Whether OCR was involved, which
pages were read, and which check corroborated each order are recorded in the QA
report.

## Finding the right listing page

DoT files orders on period pages — "(July – Sept 2026)", "(Apr – Jun 2026)",
"(older than 2025)" — and adds new ones as time passes. Asking for a month does
not scan all of them:

* Pages are discovered **two ways**: a search, and the `Data Services` document
  category. Either alone would find today's four pages; together they survive a
  retitle.
* Each page's title is parsed for a period. The naming varies a great deal —
  `(June-Sep 2026)`, `(Oct to Dec 2023)`, `(Jan-May 2026)`, `(May 2025)`,
  `(June 2025-II)` — so hyphens, en-dashes, "to", abbreviated and full month
  names, single months and multi-year spans all parse.
* Pages are then **ranked** against the month you asked for: the page whose
  period covers it, then the next period along (an order dated 30 September is
  published in October and filed on the next page), then anything open-ended or
  unparseable, then the newest page — because until DoT creates the next period
  page, new orders are appended to the current one.

**Titles are only hints.** "older than 2025" in fact holds January to July 2025.
So what each page really contains is recorded as it is read, observation
overrides the title from then on, and if the ranked pages yield nothing the
collector falls back to reading every page. Correctness never depends on DoT's
naming.

In practice this takes building August 2026 from four listing pages and ~475
metadata requests down to **one page**.

## Search

The question this answers is *"has this domain been blocked before, and under
which order?"* — and, second, getting back to the original DoT links.

Two tiers, labelled in the results:

* **Catalogued** — every order DoT has published (~475), from the local index
  with no downloads: title, case number, court, date, the PDF link, and the
  reconstructed public page link.
* **Extracted** — months you have built, which add the order's real date, block
  vs unblock, and **every URL extracted from it**. The back-catalogue job
  promotes everything to this tier.

```
vegamovies                    anywhere - title, case, court, party or blocked URL
url:vegamovies                blocked URLs only
case:"CS(COMM) 331"           case number, punctuation ignored
court:delhi                   court
party:fabindia                the parties named in the subject
date:2026-08                  a month, a year, a day, or 2026-08-05..2026-08-31
action:unblock                unblocking instructions
month:2026-08                 orders in one compilation
order:9                       the ninth order of its compilation
```

Results link to the order PDF, to its page on dot.gov.in, to the quarter listing
it belongs to, and to the month package containing it.

### The dot.gov.in link trick

The site's public URLs carry an obfuscated suffix —
`...-july-sept-2026-cTO2UzNtQWa`. That is `base64("id-75697")` with padding
stripped and reversed. The API only ever returns the CMS-internal URL, so
without deriving this the archive could not link back to the real page.

## Accuracy

| Month | Result |
| --- | --- |
| May 2025 (43 orders, 646 URLs) | precision **0.997**, recall **0.992** |
| August 2026 (22 orders, 1,366 URLs) | names, ordering and numbering match the reference exactly; **22 of 22 corroborated** |
| September 2026 (3 orders, 111 URLs) | **3 of 3 corroborated** |

## OCR

Some orders are scanned, and occasionally the website list itself is an image
sitting under a thin text layer of running headers.

Pages are rendered at 400 dpi and passed to the engine as **contrast-stretched
greyscale, not binarised**. That is not a detail: on the scanned annexure of
order 84598, hard-thresholding the image yields 128 usable domains where plain
greyscale yields 198 — the full declared count. Both engines here are neural and
read anti-aliased greyscale far better than a crushed bitmap.

Tesseract is the preferred engine. Install it once:

```bash
bash scripts/install_ocr.sh
```

Until then the app uses macOS Vision OCR automatically, and the QA report
records which engine read each page.

## Being a good citizen of dot.gov.in

The site's WAF blocks the whole client IP — not just the endpoint — after a
burst of requests. Every request goes through one shared, rate-limited session
(`src/dotbo/http.py`) with backoff, and results are cached in `workspace/`:

* `workspace/index.json` — the catalogue, plus what each listing page was
  observed to contain
* `workspace/pdfs/` — downloaded PDFs, keyed by attachment id
* `workspace/library/<YYYY-MM>/` — each built month's manifest, Word document
  and QA reports, about 150 KB per month

The library deliberately does **not** keep the ZIP: it is a copy of PDFs already
on disk, ~78 MB per month and about 1.8 GB across the back-catalogue. It is
rebuilt on demand instead, re-fetching any PDF that has since been evicted.

## If the launcher does not start

* **The Terminal window opens and nothing happens.** Run it from a terminal to
  see the error: `bash "Run DoT Collector.command"`.
* **"cannot be opened because it is from an unidentified developer."** macOS
  quarantines files copied from elsewhere. Right-click the file, choose Open,
  and confirm once — or run
  `xattr -d com.apple.quarantine "Run DoT Collector.command"`.
* **Port in use.** The server scans upward from 8000 for a free port and prints
  the address it picked.

## Layout

```
serve.py                    starts the local web server
src/dotbo/
  http.py                   rate-limited session
  period.py                 parses "(July - Sept 2026)" and its many cousins
  links.py                  reconstructs public dot.gov.in URLs
  catalog.py                page discovery, month-to-page routing, order index
  fetch.py                  PDF download and cache
  pdftext.py                PyMuPDF text/words/tables, OCR fallback
  ocr.py                    Tesseract / Vision, post-OCR domain repair
  letter.py                 page-1 covering-letter parser
  locate.py                 page classification and candidate regions
  urls.py                   tokenising, de-wrapping, noise filters
  extract.py                count-constrained selection and corroboration
  naming.py                 ordering rule and file naming
  docxout.py                the Word writer
  report.py                 QA report
  pipeline.py               orchestration
  library.py                stored months, ZIP rebuilt on demand
  backfill.py               resumable back-catalogue job
  search.py                 two-tier index and query language
  web/                      Starlette server, page, stylesheet, bundled fonts
tests/
  test_units.py             parsing rules, with the corpus cases that taught them
  test_period.py            period parsing, link derivation, month routing
  test_archive.py           library, back-catalogue, search queries
  test_golden_may2025.py    precision/recall gate vs the May reference
  test_golden_aug2026.py    naming/ordering vs the August reference
  eval_may.py               the same May scoring, runnable on its own
```

## Development

```bash
.venv/bin/python -m pytest tests -q      # 89 tests
.venv/bin/python tests/eval_may.py       # per-order precision/recall detail
.venv/bin/python serve.py --no-browser   # run the server directly
```

Typefaces are bundled under the SIL Open Font Licence 1.1 — see
`src/dotbo/web/static/fonts/OFL.txt`.
