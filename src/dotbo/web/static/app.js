/* DoT Blocking Order Collector - front end.
   No framework, no build step. Progress arrives over Server-Sent Events. */

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, kids = []) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const kid of [].concat(kids)) node.append(kid);
  return node;
};
const num = (n) => (n === null || n === undefined || n === "" ? "" : String(n));

const STAGES = [
  ["catalogue", "Checking the DoT catalogue"],
  ["download", "Downloading order PDFs"],
  ["read", "Reading covering letters"],
  ["extract", "Extracting blocked URLs"],
  ["write", "Writing the ZIP, Word document and QA report"],
];

let state = { months: [], backfillJob: null };

/* ------------------------------------------------------------------ tabs */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("is-current"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("is-current"));
    tab.classList.add("is-current");
    $(`#panel-${tab.dataset.panel}`).classList.add("is-current");
  });
});

/* ------------------------------------------------------------- bootstrap */

async function bootstrap() {
  const data = await (await fetch("/api/bootstrap")).json();
  state.months = data.months;

  const now = new Date();
  const monthSel = $("#month");
  data.months.forEach((name, i) => monthSel.append(el("option", { value: i + 1, textContent: name })));
  monthSel.value = now.getMonth() + 1;
  $("#year").value = now.getFullYear();
  $("#output").value = data.output_dir || `${data.home || ""}`;
  if (!$("#output").value) $("#output").value = "~/Downloads";

  const ocr = data.ocr || {};
  $("#ocr-status").textContent =
    ocr.engine === "tesseract"
      ? `OCR engine: ${ocr.detail}`
      : ocr.engine === "vision"
      ? "Tesseract is not installed, so scanned pages are read with macOS Vision OCR. Run scripts/install_ocr.sh once to add Tesseract."
      : ocr.detail || "";

  renderLibrary(data.library || []);
  $("#backfill-pending").textContent =
    data.pending_months > 0
      ? `${data.pending_months} months not yet built.`
      : "Every published month has been built.";
  $("#search-scope").textContent =
    `${data.catalogued} orders catalogued; ${data.indexed_urls} URLs searchable from months already built.`;
}

/* ----------------------------------------------------------------- build */

$("#build").addEventListener("click", async (event) => {
  event.preventDefault();
  const days = $("#days").value.split(/[,\s]+/).map((d) => parseInt(d, 10)).filter(Boolean);
  const body = {
    year: parseInt($("#year").value, 10),
    month: parseInt($("#month").value, 10),
    days,
    output_dir: $("#output").value,
    use_ocr: $("#use-ocr").checked,
    redownload: $("#redownload").checked,
    refresh_index: $("#refresh-index").checked,
    look_ahead_days: parseInt($("#lookahead").value, 10) || 31,
  };

  $("#build").disabled = true;
  $("#build-error").hidden = true;
  $("#result").hidden = true;
  $("#progress").hidden = false;
  $("#live").hidden = true;
  $("#live tbody").replaceChildren();
  renderStages(null);

  const started = await (await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })).json();

  if (started.error) return failBuild(started.error);
  follow(started.job, {
    onProgress: (e) => {
      $("#progress-detail").textContent = e.detail;
      $("#progress-pct").textContent = `${Math.round(e.fraction * 100)}%`;
      $("#bar-fill").style.width = `${e.fraction * 100}%`;
      $("#progress-message").textContent = e.message || "";
      renderStages(e.stage);
    },
    onOrder: (row) => {
      $("#live").hidden = false;
      $("#live tbody").prepend(
        el("tr", {}, [
          el("td", { className: "num", textContent: row.index }),
          el("td", { className: "mono", textContent: row.filename }),
          el("td", { className: "num", textContent: row.urls }),
          el("td", { className: "num", textContent: num(row.declared) }),
          el("td", { textContent: row.verified ? row.note : "—" }),
        ])
      );
    },
    onDone: (payload) => {
      $("#build").disabled = false;
      if (payload.error) return failBuild(payload.error);
      showResult(payload.result, started.job);
      refreshLibrary();
    },
  });
});

function failBuild(message) {
  $("#build").disabled = false;
  $("#progress").hidden = true;
  $("#build-error").hidden = false;
  $("#build-error").textContent = message;
}

function renderStages(current) {
  const at = STAGES.findIndex(([key]) => key === current);
  $("#stages").replaceChildren(
    ...STAGES.map(([key, label], i) => {
      const done = at > i || current === "done";
      const now = at === i;
      return el("li", { className: done ? "is-done" : now ? "is-current" : "" }, [
        el("span", { className: "glyph", textContent: done ? "✓" : now ? "▸" : "○" }),
        el("span", { textContent: label }),
      ]);
    })
  );
}

function showResult(result, jobId) {
  if (!result) return;
  $("#progress").hidden = true;
  $("#result").hidden = false;
  $("#result-line").textContent =
    `${result.label} is ready — ${result.orders} orders, ${result.urls} URLs, ` +
    `${result.verified} of ${result.orders} counts independently verified.`;
  $("#fig-orders").textContent = result.orders;
  $("#fig-urls").textContent = result.urls;
  $("#fig-verified").textContent = `${result.verified}/${result.orders}`;
  $("#written-to").textContent = `Also written to ${result.output_dir}`;

  const labels = {
    zip: "ZIP of renamed PDFs",
    docx: "Consolidated Word document",
    report_html: "QA report (HTML)",
    report_csv: "QA report (CSV)",
  };
  $("#downloads").replaceChildren(
    ...(result.files || []).map((kind) =>
      el("a", { href: `/api/job/${jobId}/file/${kind}` }, [
        el("span", { textContent: labels[kind] || kind }),
        el("span", { className: "name", textContent: "download" }),
      ])
    )
  );

  $("#orders tbody").replaceChildren(
    ...(result.rows || []).map((r) =>
      el("tr", {}, [
        el("td", { className: "num", textContent: r.no }),
        el("td", { className: "mono", textContent: r.filename }),
        el("td", { className: "mono", textContent: r.order_date }),
        el("td", { textContent: r.case_no }),
        el("td", { textContent: r.action }),
        el("td", { className: "num", textContent: r.urls_found }),
        el("td", { className: "num", textContent: num(r.declared_count) }),
        el("td", { textContent: r.corroboration || "—" }),
        el("td", { textContent: r.ocr_engine || "" }),
      ])
    )
  );
}

/* ------------------------------------------------------------------ SSE */

function follow(jobId, handlers) {
  const source = new EventSource(`/api/job/${jobId}/stream`);
  source.onmessage = (message) => {
    const payload = JSON.parse(message.data);
    if (payload.type === "progress") handlers.onProgress?.(payload);
    else if (payload.type === "order") handlers.onOrder?.(payload.order);
    else if (payload.type === "error") handlers.onError?.(payload);
    else if (payload.type === "done") {
      source.close();
      handlers.onDone?.(payload);
    }
  };
  source.onerror = () => source.close();
  return source;
}

/* -------------------------------------------------------------- archive */

async function refreshLibrary() {
  const data = await (await fetch("/api/library")).json();
  renderLibrary(data.months || []);
}

function renderLibrary(months) {
  $("#library-empty").hidden = months.length > 0;
  $("#library tbody").replaceChildren(
    ...months.map((m) =>
      el("tr", {}, [
        el("td", { textContent: m.label }),
        el("td", { className: "num", textContent: m.orders }),
        el("td", { className: "num", textContent: m.urls }),
        el("td", { className: "num", textContent: `${m.verified}/${m.orders}` }),
        el("td", { className: "mono", textContent: m.built_on }),
        el("td", {}, [
          el("span", { className: "hit__links" }, [
            el("a", { href: `/api/library/${m.slug}/zip`, textContent: "ZIP" }),
            el("a", { href: `/api/library/${m.slug}/docx`, textContent: "Word" }),
            el("a", { href: `/api/library/${m.slug}/report_html`, textContent: "QA" }),
          ]),
        ]),
      ])
    )
  );
}

$("#backfill").addEventListener("click", async () => {
  $("#backfill").disabled = true;
  $("#backfill-stop").hidden = false;
  $("#backfill-progress").hidden = false;
  const started = await (await fetch("/api/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ use_ocr: $("#use-ocr").checked }),
  })).json();
  state.backfillJob = started.job;
  follow(started.job, {
    onProgress: (e) => {
      $("#backfill-detail").textContent = `${e.label} — ${e.detail}`;
      $("#backfill-pct").textContent = `${Math.round(e.fraction * 100)}%`;
      $("#backfill-fill").style.width = `${e.fraction * 100}%`;
      $("#backfill-message").textContent = e.message || "";
    },
    onDone: (payload) => {
      $("#backfill").disabled = false;
      $("#backfill-stop").hidden = true;
      const built = payload.result?.built?.length || 0;
      $("#backfill-message").textContent =
        `${built} months built${payload.result?.stopped ? " before stopping" : ""}.`;
      refreshLibrary();
      bootstrap();
    },
  });
});

$("#backfill-stop").addEventListener("click", async () => {
  if (!state.backfillJob) return;
  await fetch(`/api/job/${state.backfillJob}/stop`, { method: "POST" });
  $("#backfill-stop").disabled = true;
  $("#backfill-message").textContent = "Stopping after the current month…";
});

/* --------------------------------------------------------------- search */

async function runSearch() {
  const query = $("#q").value.trim();
  if (!query) return;
  const data = await (await fetch(`/api/search?q=${encodeURIComponent(query)}`)).json();
  renderHits(data);
}

$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch();
});

// Implicit form submission does not fire reliably from a type=search field,
// and pressing Enter is what everyone actually does.
$("#q").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    runSearch();
  }
});

function renderHits(data) {
  const results = $("#search-results");
  if (!data.hits.length) {
    results.replaceChildren(el("p", { className: "note", textContent: `Nothing matched ${data.query}.` }));
    return;
  }
  results.replaceChildren(
    el("p", { className: "note", textContent: `${data.total} order${data.total === 1 ? "" : "s"} matched.` }),
    ...data.hits.map(renderHit)
  );
}

function renderHit(hit) {
  const meta = [];
  meta.push(el("span", { className: `tag ${hit.tier === "built" ? "tag--built" : ""}`,
                         textContent: hit.tier === "built" ? "extracted" : "catalogued" }));
  if (hit.case) meta.push(el("span", { textContent: hit.case }));
  if (hit.court) meta.push(el("span", { textContent: hit.court }));
  if (hit.action === "unblock") meta.push(el("span", { className: "tag", textContent: "unblocking" }));
  if (hit.month_label) meta.push(el("span", { textContent: `${hit.month_label} · no. ${hit.index}` }));
  if (hit.urls) meta.push(el("span", { textContent: `${hit.urls} URLs` }));

  const urls = (hit.matched || []).slice(0, 12);
  const links = [];
  if (hit.pdf_url) links.push(el("a", { href: hit.pdf_url, target: "_blank", rel: "noreferrer", textContent: "Order PDF" }));
  if (hit.public_url) links.push(el("a", { href: hit.public_url, target: "_blank", rel: "noreferrer", textContent: "Page on dot.gov.in" }));
  if (hit.listing_url) links.push(el("a", { href: hit.listing_url, target: "_blank", rel: "noreferrer", textContent: "Quarter listing" }));
  if (hit.month_slug) links.push(el("a", { href: `/api/library/${hit.month_slug}/zip`, textContent: "Download that month" }));

  return el("article", { className: "hit" }, [
    el("div", { className: "hit__head" }, [
      el("h3", { className: "hit__title", textContent: hit.title || hit.case || `Order ${hit.attachment_id}` }),
      el("span", { className: "hit__date", textContent: hit.date || "" }),
    ]),
    el("p", { className: "hit__meta" }, meta),
    urls.length
      ? el("ul", { className: "hit__urls" }, [
          ...urls.map((u) => el("li", { textContent: u })),
          ...(hit.matched.length > urls.length
            ? [el("li", { className: "hit__more", textContent: `and ${hit.matched.length - urls.length} more` })]
            : []),
        ])
      : el("span"),
    el("p", { className: "hit__links" }, links),
  ]);
}

bootstrap();
