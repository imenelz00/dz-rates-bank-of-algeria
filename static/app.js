"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts) {
  const r = await fetch("/api" + path, opts);
  if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
  return r.json();
}
const fmtN = v => v == null ? "—" : v.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
const fmtD = iso => iso ? new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }) : "—";
const fmtDT = iso => iso ? new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : "—";
const NA = '<span class="na">n/a</span>';
const TYPE_LABEL = { reference: "Reference", buy: "Purchase (Achat)", sell: "Selling (Vente)", annual_avg: "Annual average", annual_eop: "Year-end" };
const SHORT = { reference: "Reference", buy: "Purchase", sell: "Selling", annual_avg: "Average", annual_eop: "Year-end" };
const COLOR = { reference: "--series-ref", buy: "--series-1", sell: "--series-2", annual_avg: "--series-ref", annual_eop: "--series-3" };
const STATUS_ICON = { success: "✓", partial: "◐", failed: "✕", running: "…" };
const chip = s => `<span class="chip ${s}"><span aria-hidden="true">${STATUS_ICON[s] || ""}</span>${s[0].toUpperCase() + s.slice(1)}</span>`;

let meta, current, charts = {}, hist = { offset: 0, limit: 25, sort: "desc", total: 0 };

/* ---------- source labels: how a value got into the app ---------- */
const srcLabel = r => r.granularity === "annual" ? ["Annual · imported", "annual"]
  : r.source_kind.startsWith("mock") ? ["Demo", "demo"] : ["Daily · collected", ""];
const DATASETS = () => ({
  "": { label: "All", types: [] },
  daily: { label: meta.demo ? "Daily · demo" : "Daily · collected", types: ["reference", "buy", "sell"] },
  annual: { label: "Annual · imported", types: ["annual_avg", "annual_eop"] },
});

/* ---------- tabs ---------- */
const TABS = ["overview", "history", "sync"];
function showTab() {
  const t = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview";
  $$("[data-panel]").forEach(p => p.hidden = p.dataset.panel !== t);
  $$(".tabs a").forEach(a => {
    const on = a.dataset.tab === t;
    a.classList.toggle("active", on);
    on ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  });
  if (t === "overview") Object.values(charts).forEach(c => c.resize());
}
addEventListener("hashchange", showTab);

/* ---------- overview: latest rates ---------- */
async function loadCurrent() {
  current = await api("/current");
  $("#latest tbody").innerHTML = current.currencies.map(c => {
    const r = c.reference, b = c.buy, s = c.sell;
    return `<tr><td><strong>${c.currency}</strong> <span class="muted">${esc(c.name)}</span></td>
      <td class="num">${r ? fmtN(r.value) : NA}</td><td class="num">${b ? fmtN(b.value) : NA}</td><td class="num">${s ? fmtN(s.value) : NA}</td>
      <td>${r ? fmtD(r.publication_date) : "—"}</td><td>${b ? fmtD(b.publication_date) : "—"}</td><td>${b ? fmtD(b.value_date) : "—"}</td></tr>`;
  }).join("");
  const stamps = current.currencies.flatMap(c => [c.reference, c.buy, c.sell]).filter(Boolean).map(x => x.retrieved_at).sort();
  $("#latestNote").textContent = "Reference rates and purchase/selling quotations are published separately and can carry different dates." +
    (stamps.length ? ` Last retrieved ${fmtDT(stamps[stamps.length - 1])} UTC.` : "");
  const la = current.last_attempt;
  $("#syncSummary").innerHTML = current.last_successful_sync
    ? `Last successful sync ${fmtDT(current.last_successful_sync)} UTC` + (la && la.status !== "success" ? ` · latest attempt ${chip(la.status)}` : "")
    : "No successful sync yet";
}

/* ---------- charts ---------- */
const crosshair = {
  id: "crosshair", afterDatasetsDraw(c) {
    const a = c.tooltip && c.tooltip.getActiveElements();
    if (!a || !a.length) return;
    const { ctx, chartArea: ca } = c, x = a[0].element.x;
    ctx.save(); ctx.strokeStyle = css("--text-3"); ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, ca.top); ctx.lineTo(x, ca.bottom); ctx.stroke(); ctx.restore();
  }
};
const endLabels = {  // direct labels at the line ends (higher series above, lower below)
  id: "endLabels", afterDatasetsDraw(c) {
    if (c.data.datasets.length < 2) return;
    const pts = c.data.datasets.map((ds, i) => {
      let k = ds.data.length - 1; while (k >= 0 && ds.data[k].y == null) k--;
      const el = c.getDatasetMeta(i).data[k];
      return el ? { ds, el, y: ds.data[k].y } : null;
    }).filter(Boolean);
    if (!pts.length) return;
    const top = Math.max(...pts.map(p => p.y)), { ctx } = c;
    ctx.save(); ctx.font = "600 12px system-ui"; ctx.fillStyle = css("--text-2"); ctx.textAlign = "right";
    pts.forEach(p => ctx.fillText(p.ds.label, p.el.x - 4, p.el.y + (p.y === top ? -12 : 20)));
    ctx.restore();
  }
};
function styleChart(c) {
  const grid = css("--line-soft"), tx = css("--text-3");
  c.data.datasets.forEach(ds => { ds.borderColor = css(COLOR[ds.key]); });
  c.options.scales.x.grid.color = grid; c.options.scales.y.grid.color = grid;
  c.options.scales.x.ticks.color = tx; c.options.scales.y.ticks.color = tx;
  c.options.plugins.legend.labels.color = css("--text-2");
  c.update("none");
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => Object.values(charts).forEach(styleChart));

const tickFmt = spanDays => v => new Date(v).toLocaleDateString("en-GB",
  spanDays > 400 ? { month: "short", year: "2-digit", timeZone: "UTC" } : { day: "2-digit", month: "short", timeZone: "UTC" });
function monthTicks(scale) {
  const span = (scale.max - scale.min) / 864e5, out = [];
  if (span <= 62) { for (let v = scale.min; v <= scale.max; v += 7 * 864e5) out.push({ value: v }); }
  else {
    const step = span > 1500 ? 12 : span > 700 ? 6 : span > 300 ? 3 : 1;
    const d = new Date(scale.min); let y = d.getUTCFullYear(), m = d.getUTCMonth() + (d.getUTCDate() > 1 ? 1 : 0);
    if (step > 1) m = Math.ceil(m / step) * step;
    for (; ; m += step) { const v = Date.UTC(y, m, 1); if (v > scale.max) break; out.push({ value: v }); }
  }
  scale.ticks = out;
}
function yearTicks(scale) {
  const out = [];
  for (let y = Math.ceil(scale.min / 5) * 5; y <= scale.max; y += 5) out.push({ value: y });
  scale.ticks = out;
}
function yTick(v) { const r = this.max - this.min; return v.toFixed(r < 0.1 ? 3 : r < 1 ? 2 : r < 20 ? 1 : 0); }

function newChart(canvas, types, annual) {
  return new Chart(canvas, {
    type: "line",
    data: {
      datasets: types.map(t => ({
        key: t, label: SHORT[t], data: [], borderWidth: 2, tension: 0, spanGaps: false,
        pointRadius: c => c.dataset.data.filter(p => p.y != null).length <= 40 ? 3 : 0,  // few points: draw them
        pointBackgroundColor: c => css(COLOR[c.dataset.key]), pointBorderColor: c => css(COLOR[c.dataset.key]),
        pointHoverRadius: 4, pointHoverBackgroundColor: c => css(COLOR[c.dataset.key]),
      }))
    },
    options: {
      parsing: false, animation: false, maintainAspectRatio: false, responsive: true,
      interaction: { mode: "index", intersect: false },
      layout: { padding: { right: 6 } },
      scales: {
        x: annual ? { type: "linear", grid: { drawTicks: false }, border: { display: false }, afterBuildTicks: yearTicks, ticks: { callback: v => String(v) } }
          : { type: "linear", grid: { drawTicks: false }, border: { display: false }, afterBuildTicks: monthTicks, ticks: { callback: tickFmt(365) } },
        y: { grace: "8%", border: { display: false }, grid: {}, ticks: { callback: yTick } },
      },
      plugins: {
        legend: { display: types.length > 1, labels: { usePointStyle: true, pointStyle: "rectRounded", boxWidth: 10, boxHeight: 4, color: css("--text-2") } },
        tooltip: {
          callbacks: {
            title: it => annual ? "Year " + it[0].parsed.x : fmtD(new Date(it[0].parsed.x).toISOString().slice(0, 10)),
            label: it => `${it.dataset.label}: ${fmtN(it.parsed.y)} DZD`,
          }
        },
      },
    },
    plugins: annual ? [crosshair] : [crosshair, endLabels],
  });
}
const chartCard = (id, title, empty = "No data in this range") => `<div class="chart-card"><h3>${title}</h3><div class="chart-box">
  <canvas id="c-${id}" role="img" aria-label="${title}"></canvas><div class="chart-empty" id="e-${id}" hidden>${empty}</div></div></div>`;

let DAILY_DEFS = [], ANNUAL_DEFS = [];
function buildCharts() {
  const cs = meta.currencies.map(c => c.code);
  DAILY_DEFS = [...cs.map(c => ({ id: `${c}-ref`, cur: c, types: ["reference"], title: `${c} · reference rate` })),
    ...cs.map(c => ({ id: `${c}-bs`, cur: c, types: ["buy", "sell"], title: `${c} · purchase / selling` }))];
  ANNUAL_DEFS = cs.map(c => ({ id: `${c}-yr`, cur: c, types: ["annual_avg", "annual_eop"], title: `${c} · annual average and year-end rate` }));
  $("#charts").innerHTML = DAILY_DEFS.map(d => chartCard(d.id, d.title)).join("");
  $("#annualCharts").innerHTML = ANNUAL_DEFS.map(d => chartCard(d.id, d.title, "No annual data imported")).join("");
  DAILY_DEFS.forEach(d => charts[d.id] = newChart($("#c-" + d.id), d.types, false));
  ANNUAL_DEFS.forEach(d => charts[d.id] = newChart($("#c-" + d.id), d.types, true));
}

let range = { from: null, to: null };
async function loadCharts() {
  const q = (range.from ? `&start=${range.from}` : "") + (range.to ? `&end=${range.to}` : "");
  const span = range.from && range.to ? (Date.parse(range.to) - Date.parse(range.from)) / 864e5 : 9999;
  let maxPts = 0;
  await Promise.all(DAILY_DEFS.map(async d => {
    const s = await api(`/series?currency=${d.cur}&types=${d.types.join(",")}${q}`);
    const c = charts[d.id]; let any = false;
    c.data.datasets.forEach(ds => {
      ds.data = (s[ds.key] || []).map(([dt, v]) => ({ x: Date.parse(dt), y: v }));
      any = any || ds.data.some(p => p.y != null);
      maxPts = Math.max(maxPts, ds.data.length);
    });
    c.options.scales.x.ticks.callback = tickFmt(span);
    const xs = c.data.datasets.flatMap(ds => ds.data.filter(p => p.y != null).map(p => p.x));
    const pad = 864e5;  // one day of breathing room so a lone point is not cut off at the edge
    c.options.scales.x.min = range.from ? Date.parse(range.from) - pad : (xs.length ? Math.min(...xs) - pad : undefined);
    c.options.scales.x.max = range.to ? Date.parse(range.to) + pad : (xs.length ? Math.max(...xs) + pad : undefined);
    const ys = c.data.datasets.flatMap(ds => ds.data.filter(p => p.y != null).map(p => p.y));
    c.options.scales.y.suggestedMin = ys.length ? Math.min(...ys) - 0.3 : undefined;  // never zoom into noise
    c.options.scales.y.suggestedMax = ys.length ? Math.max(...ys) + 0.3 : undefined;
    $("#e-" + d.id).hidden = any;
    styleChart(c);
  }));
  const note = $("#sparseNote");
  note.hidden = maxPts >= 20;
  note.textContent = `Only ${maxPts} day(s) of daily data are stored so far, so the ranges look alike. History builds up with each daily sync.`;
}
async function loadAnnual() {
  const data = await api("/annual");
  ANNUAL_DEFS.forEach(d => {
    const c = charts[d.id], s = data[d.cur] || {}; let any = false;
    c.data.datasets.forEach(ds => {
      ds.data = (s[ds.key] || []).map(([y, v]) => ({ x: y, y: v }));
      any = any || ds.data.length > 0;
    });
    const xs = c.data.datasets.flatMap(ds => ds.data.map(p => p.x));
    c.options.scales.x.min = xs.length ? Math.min(...xs) : undefined;
    c.options.scales.x.max = xs.length ? Math.max(...xs) : undefined;
    $("#e-" + d.id).hidden = any;
    styleChart(c);
  });
}
function setRangeDays(days) {
  const last = meta.coverage.reference ? meta.coverage.reference.to : new Date().toISOString().slice(0, 10);
  range.to = last;
  if (days === "all") range.from = meta.coverage.reference ? meta.coverage.reference.from : null;
  else { const d = new Date(last + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() - Number(days)); range.from = d.toISOString().slice(0, 10); }
  $("#chFrom").value = range.from || ""; $("#chTo").value = range.to || "";
}
$$(".seg").forEach(b => b.onclick = () => {
  $$(".seg").forEach(x => x.classList.toggle("active", x === b));
  setRangeDays(b.dataset.range); loadCharts();
});
["#chFrom", "#chTo"].forEach(id => $(id).onchange = () => {
  range.from = $("#chFrom").value || null; range.to = $("#chTo").value || null;
  $$(".seg").forEach(x => x.classList.remove("active")); loadCharts();
});

/* ---------- history ---------- */
function fillTypeOptions() {
  const types = DATASETS()[$("#fData").value].types;
  const all = types.length ? types : Object.keys(TYPE_LABEL);
  $("#fType").innerHTML = '<option value="">All</option>' + all.map(t => `<option value="${t}">${TYPE_LABEL[t]}</option>`).join("");
}
function histParams(paged) {
  const p = new URLSearchParams();
  const types = $("#fType").value || DATASETS()[$("#fData").value].types.join(",");
  const m = { currency: $("#fCur").value, types, start: $("#fFrom").value, end: $("#fTo").value };
  Object.entries(m).forEach(([k, v]) => v && p.set(k, v));
  p.set("sort", hist.sort);
  if (paged) { p.set("limit", hist.limit); p.set("offset", hist.offset); }
  return p.toString();
}
async function loadHistory() {
  const d = await api("/rates?" + histParams(true));
  hist.total = d.total;
  $("#histTable tbody").innerHTML = d.items.length ? d.items.map(r => {
    const annual = r.granularity === "annual", [lbl, cls] = srcLabel(r);
    return `<tr><td><strong>${r.currency}</strong></td><td>${TYPE_LABEL[r.rate_type] || r.rate_type}</td>
      <td class="num">${fmtN(r.value)}${r.flagged ? `<span class="flag" title="${esc(r.flag_reason)}" aria-label="Flagged: ${esc(r.flag_reason)}">⚠</span>` : ""}</td>
      <td>${annual ? "Year " + r.publication_date.slice(0, 4) : fmtD(r.publication_date)}</td>
      <td>${annual ? "—" : r.value_date !== r.publication_date ? fmtD(r.value_date) : '<span class="na">same day</span>'}</td>
      <td>${fmtDT(r.retrieved_at)}</td><td><span class="src ${cls}">${lbl}</span></td></tr>`;
  }).join("") : `<tr><td colspan="7" class="na" style="padding:20px 14px">No records match these filters.</td></tr>`;
  const from = d.total ? hist.offset + 1 : 0, to = Math.min(hist.offset + hist.limit, d.total);
  $("#pageInfo").textContent = `${from}–${to} of ${d.total.toLocaleString()} records`;
  $("#prevBtn").disabled = hist.offset === 0; $("#nextBtn").disabled = to >= d.total;
  $("#sortArrow").textContent = hist.sort === "desc" ? "↓" : "↑";
  const q = histParams(false);
  $("#expCsv").href = "/api/export.csv?" + q; $("#expXlsx").href = "/api/export.xlsx?" + q;
}
$("#fData").onchange = () => { fillTypeOptions(); hist.offset = 0; loadHistory(); };
["#fCur", "#fType", "#fFrom", "#fTo"].forEach(id => $(id).onchange = () => { hist.offset = 0; loadHistory(); });
$("#fReset").onclick = () => { $("#fData").value = ""; fillTypeOptions(); ["#fCur", "#fFrom", "#fTo"].forEach(id => $(id).value = ""); hist.offset = 0; loadHistory(); };
$("#sortBtn").onclick = () => { hist.sort = hist.sort === "desc" ? "asc" : "desc"; hist.offset = 0; loadHistory(); };
$("#prevBtn").onclick = () => { hist.offset = Math.max(0, hist.offset - hist.limit); loadHistory(); };
$("#nextBtn").onclick = () => { hist.offset += hist.limit; loadHistory(); };

/* ---------- synchronization ---------- */
function renderSyncFacts() {
  const la = current && current.last_attempt;
  $("#facts").innerHTML = `
    <dt>Mode</dt><dd>${meta.demo ? "Demo (generated daily values)" : "Live (Bank of Algeria publications)"}</dd>
    <dt>Schedule</dt><dd>Daily at ${esc(meta.sync_time)} (${esc(meta.timezone)})</dd>
    <dt>Last successful sync</dt><dd>${current && current.last_successful_sync ? fmtDT(current.last_successful_sync) + " UTC" : "None yet"}</dd>
    <dt>Latest attempt</dt><dd>${la ? chip(la.status) + " · " + fmtDT(la.at) + " UTC" : "None yet"}</dd>`;
  const order = Object.keys(TYPE_LABEL).filter(t => meta.coverage[t]);
  $("#coverage tbody").innerHTML = order.map(t => {
    const c = meta.coverage[t], annual = t.startsWith("annual");
    const [lbl, cls] = annual ? ["Annual · imported", "annual"] : meta.demo ? ["Demo", "demo"] : ["Daily · collected", ""];
    return `<tr><td>${TYPE_LABEL[t]}</td><td><span class="src ${cls}">${lbl}</span></td>
      <td>${annual ? c.from.slice(0, 4) : fmtD(c.from)}</td><td>${annual ? c.to.slice(0, 4) : fmtD(c.to)}</td><td class="num">${c.count.toLocaleString()}</td></tr>`;
  }).join("") || '<tr><td colspan="5" class="na" style="padding:20px 14px">Nothing stored yet.</td></tr>';
}
async function loadLog() {
  const runs = await api("/sync-runs?limit=15");
  const tb = $("#logTable tbody"); tb.innerHTML = "";
  runs.forEach(r => {
    const dur = r.finished_at ? Math.max(0, Math.round((Date.parse(r.finished_at) - Date.parse(r.started_at)) / 1000)) + " s" : "…";
    const tr = document.createElement("tr"); tr.className = "clickable"; tr.tabIndex = 0;
    tr.innerHTML = `<td>${r.id}</td><td>${r.trigger}</td><td>${fmtDT(r.started_at)}</td><td>${dur}</td><td>${chip(r.status)}</td>
      <td class="num">${r.records_found.toLocaleString()}</td><td class="num">${r.records_inserted.toLocaleString()}</td>
      <td class="num">${r.records_updated.toLocaleString()}</td><td>${r.status === "partial" || r.status === "failed" ? '<button class="link" data-retry>Retry failed</button>' : ""}</td>`;
    const toggle = async () => {
      const nxt = tr.nextElementSibling;
      if (nxt && nxt.classList.contains("detail")) { nxt.remove(); return; }
      const d = await api("/sync-runs/" + r.id);
      const det = document.createElement("tr"); det.className = "detail";
      det.innerHTML = `<td colspan="9"><ul>${d.items.map(i => `<li>${chip(i.status)} <strong>${esc(i.source_kind)}</strong> · ${i.currency} — ${i.records_found} found, ${i.records_inserted} new, ${i.records_updated} updated${i.error_message ? `<br><span style="color:var(--bad)">${esc(i.error_message)}</span>` : ""}${i.details ? `<br>${esc(i.details)}` : ""}</li>`).join("")}</ul></td>`;
      tr.after(det);
    };
    tr.onclick = e => { if (e.target.closest("[data-retry]")) return; toggle(); };
    tr.onkeydown = e => { if (e.key === "Enter") toggle(); };
    tr.querySelector("[data-retry]")?.addEventListener("click", () => act(`/sync-runs/${r.id}/retry`));
    tb.append(tr);
  });
}
async function refreshAll() {
  meta = await api("/meta"); showAlerts();
  await Promise.all([loadCurrent(), loadLog(), loadCharts(), loadAnnual(), loadHistory()]);
  renderSyncFacts();
}
async function act(path) {
  const b = $("#syncBtn"); b.disabled = true; b.textContent = "Syncing…";
  try { await api(path, { method: "POST" }); } catch (e) { alert(e.message); }
  b.disabled = false; b.textContent = "Sync now"; await refreshAll();
}
$("#syncBtn").onclick = () => act("/sync");
function showAlerts() {
  $("#demoStrip").hidden = !meta.demo;
  const a = $("#alertBar"); a.hidden = !meta.alert;
  a.textContent = `Synchronization has failed ${meta.failure_streak} times in a row. Existing data is preserved; see the Synchronization tab.`;
}

(async function init() {
  meta = await api("/meta");
  $("#fData").innerHTML = Object.entries(DATASETS()).map(([k, v]) => `<option value="${k}">${v.label}</option>`).join("");
  fillTypeOptions();
  meta.currencies.forEach(c => $("#fCur").insertAdjacentHTML("beforeend", `<option value="${c.code}">${c.code} — ${esc(c.name)}</option>`));
  showAlerts(); buildCharts(); setRangeDays(365); showTab();
  await Promise.all([loadCurrent(), loadCharts(), loadAnnual(), loadHistory(), loadLog()]);
  renderSyncFacts();
})().catch(e => document.body.insertAdjacentHTML("afterbegin", `<div class="alert">Could not load data: ${esc(e.message)}</div>`));
