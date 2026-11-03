// The spec-drift dashboard. scripts/spec-drift.py writes the data line that calls start().
function boot(DATA) {
const R = DATA.rows;
const N = R.length;
const KIND = { spec: "Spec change", code: "Code track", close: "Close the day", other: "Other", origin: "Plan as written" };
const KVAR = { spec: "var(--spec)", code: "var(--code)", close: "var(--close)", other: "var(--other)", origin: "var(--muted)" };
const fmt = (x, d = 1) => x == null ? "–" : Number(x).toFixed(d);
const band = b => b == null ? "–" : `${fmt(b[0])}–${fmt(b[1])}°`;
// Where a merge's gap sits against its shuffled counts' 95% band, endpoints inside. It says how the
// gap compares, not that the code is right or that the difference is significant.
const bandPlace = r => r.gap_full == null || r.gap_chance_band == null ? "comparison unavailable" :
  `${r.gap_full < r.gap_chance_band[0] ? "below" : r.gap_full > r.gap_chance_band[1] ? "above" : "inside"} the shuffled-count reference band`;
// PR titles come from anyone who opens a pull request, so everything from the data is escaped.
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const md = s => esc(s)
  .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
  .replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
  .replace(/\*([^*\s][^*]*)\*/g, "<em>$1</em>");
const dd = n => String(n).padStart(2, "0");
const prLabel = r => r.pr ? "#" + r.pr : "start";
const dayOfFile = f => { const m = f.match(/day-(\d+)/); return m ? +m[1] : 0; };
// A day's place in the run order (plan.md): a day may run before a lower-numbered one. Mirrors ranker() in the script.
const ORDER = DATA.order || [];
const rank = d => !d ? -1 : ORDER.includes(d) ? ORDER.indexOf(d) : ORDER.length + d;
// The days that break the rising run of the order: run ahead of a lower day, or behind a higher one.
const OOO = new Set(DATA.out_of_order || []);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const shortDate = s => `${MONTHS[+s.slice(5, 7) - 1]} ${+s.slice(8)}`;
const ranBetween = d => { const i = ORDER.indexOf(d); return [ORDER[i - 1], ORDER[i + 1]].map(x => x ? `Day ${dd(x)}` : "–"); };
const NS = "http://www.w3.org/2000/svg";
let sel = N - 1;
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

/* ---------- tooltip ---------- */
const tip = document.getElementById("tip");
function showTip(ev, html) {
  tip.innerHTML = html; tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = ev.clientX + 14, y = ev.clientY + 14;
  if (x + w > innerWidth - 8) x = ev.clientX - w - 14;
  if (y + h > innerHeight - 8) y = ev.clientY - h - 14;
  tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, y) + "px";
}
const hideTip = () => { tip.hidden = true; };
function rowTip(r) {
  return `<b>${prLabel(r)} · ${KIND[r.kind]}</b><div style="margin:3px 0 6px">${esc(r.title)}</div>
  <div class="row"><span>date</span><span>${r.date}</span></div>
  <div class="row"><span>day reached</span><span>${r.day || "–"}</span></div>
  <div class="row"><span>spec names in code</span><span>${r.name_coverage == null ? "–" : fmt(r.name_coverage * 100, 1) + "%"}</span></div>
  <div class="row"><span>spec names in production</span><span>${r.prod_name_coverage == null ? "–" : fmt(r.prod_name_coverage * 100, 1) + "%"}</span></div>
  <div class="row"><span>Jaccard, spec ∩ code</span><span>${r.jaccard == null ? "–" : fmt(r.jaccard, 2)}</span></div>
  <div class="row"><span>names kept, dropped by specs</span><span>${r.names_kept ?? "–"}</span></div>
  <div class="row"><span>Spec names retired from the code</span><span>${r.names_retired ?? "–"}</span></div>
  <div class="row"><span>name-gap angle (secondary)</span><span>${fmt(r.gap_full)}°</span></div>
  <div class="row"><span>name gap by chance</span><span>${r.gap_chance == null ? "–" : fmt(r.gap_chance) + "°"} <small>(${band(r.gap_chance_band)})</small></span></div>
  <div class="row"><span>reached names in code</span><span>${r.coverage == null ? "–" : fmt(r.coverage * 100, 1) + "%"}</span></div>
  <div class="row"><span>service code out</span><span>${movedShare(r) == null ? "–" : fmt(movedShare(r)) + "%"}</span></div>`;
}

/* ---------- derived numbers ---------- */
const first = R[0], last = R[N - 1];
// A merge with no gap angle (no spec name in the code yet) takes no step, to it or from it.
const step = (a, b) => a == null || b == null ? 0 : +(b - a).toFixed(2);
const byKind = { spec: 0, code: 0, close: 0, other: 0 };
// A commit with no PR is the plan as written, as in the kind charts: it moves no kind's share.
for (let i = 1; i < N; i++) if (R[i].kind in byKind) byKind[R[i].kind] += step(R[i - 1].gap_full, R[i].gap_full);
const gapClosed = -Object.values(byKind).reduce((a, b) => a + b, 0);
// With no gap closed there is no share: "–", not NaN.
const ofGap = x => gapClosed ? `${Math.round(-x / gapClosed * 100)}%` : "–";
const firstGap = R.find(r => r.gap_full != null) || first;
const specShare = -byKind.spec / gapClosed;
const nKind = k => R.filter(r => r.kind === k).length;
// A count and its noun, singular for one: "1 day", "2 days".
const many = (n, one, more = one + "s") => `${n} ${n === 1 ? one : more}`;
let forward = 0, specEdits = 0;
R.forEach(r => {
  for (const [f, n] of Object.entries(r.spec_files || {})) {
    const d = dayOfFile(f);
    if (!d) continue;
    specEdits += n;
    if (rank(d) > rank(r.day)) forward += n;
  }
});

/* ---------- stats ---------- */
document.getElementById("stats").innerHTML = `
  <div class="stat"><span class="k">Spec names in the code · Jaccard</span>
    <span class="v num">${last.name_coverage == null ? "–" : fmt(last.name_coverage * 100, 1) + "%"} <small>· ${last.jaccard == null ? "–" : fmt(last.jaccard, 2)}</small></span>
    <span class="n">Of the backticked names the specs use today, the share the code has${last.spec_names == null ? "" : ` (${last.names_in_code} of ${last.spec_names}; in production, ${last.names_in_prod})`}; Jaccard is the names in both over the names in either.</span>
    <span class="n" style="color:var(--muted)">Name-gap angle (secondary): ${fmt(firstGap.gap_full)}° → ${fmt(last.gap_full)}°, ${fmt(gapClosed)}° closed across ${N - 1} merges; ${fmt(last.gap_chance)}° by chance (95%: ${band(last.gap_chance_band)}). ${last.names_kept == null ? "" : `${last.names_kept} names the specs dropped are still in the code. `}${last.names_retired == null ? "" : `${last.names_retired} of the names not in the code were there at an earlier merge: retired, still counted as missing. `}${last.coverage == null ? "" : `${fmt(last.coverage * 100, 1)}% of the names the reached days use are in the code.`}</span></div>
  <div class="stat"><span class="k">Share of the gap closed by the spec</span>
    <span class="v num">${ofGap(byKind.spec)}</span>
    <span class="n"><span class="sw" style="background:var(--spec)"></span>${many(nKind("spec"), "spec-change PR")} ${specShare >= 0 ? "moved the spec toward the code" : "widened the gap, naming what was not built yet"}; <span class="sw" style="background:var(--code)"></span>${many(nKind("code"), "code PR")} closed ${ofGap(byKind.code)}</span></div>
  <div class="stat"><span class="k">Spec text since ${shortDate(R[0].date)}</span>
    <span class="v num">−${last.deleted} <small>/ +${last.added} lines</small></span>
    <span class="n">of ${DATA.origin_lines} lines as written: ${Math.round(last.deleted / DATA.origin_lines * 100)}% rewritten, the rest kept</span></div>`;
document.getElementById("headline").textContent = specShare >= -byKind.code / gapClosed
  ? "The plan moved to meet the code" : "The spec leads, the code follows";
document.querySelectorAll(".vocab-n").forEach(e => { e.textContent = DATA.vocab_size; });
const merged = R.filter(r => r.pr).map(r => r.pr);
document.getElementById("pr-range").textContent = many(N - 1, "merge") + (merged.length ? `, #${Math.min(...merged)}–#${Math.max(...merged)}` : "");
document.getElementById("gap-chance").textContent = `${fmt(last.gap_chance)}° (95%: ${band(last.gap_chance_band)})`;
document.getElementById("caveats").innerHTML = ((DATA.meta || {}).caveats || []).map(c => `<li>${esc(c)}</li>`).join("");
// spec-drift.py's example-data fallback (demo: true): say so under the header until the first day spec merges.
const banner = document.getElementById("demo-banner");
banner.textContent = "Example data: this project has no day specs yet. The page will show real data after the first day spec merges.";
banner.hidden = DATA.demo !== true;

/* ---------- scrubber ---------- */
const scrub = document.getElementById("pr-scrub");
scrub.max = N - 1; scrub.value = sel;
scrub.addEventListener("input", () => setSel(+scrub.value));
let timer = null;
const playBtn = document.getElementById("play");
function stop() { clearInterval(timer); timer = null; playBtn.textContent = "▶ Replay"; }
playBtn.addEventListener("click", () => {
  if (timer) return stop();
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { setSel(N - 1); return; }
  playBtn.textContent = "❚❚ Pause";
  let i = sel >= N - 1 ? 0 : sel;
  setSel(i);
  timer = setInterval(() => { i++; if (i >= N) return stop(); setSel(i); }, 260);
});

/* ---------- sparklines beside the selected merge ---------- */
const SPARK_W = 96, SPARK_H = 20;
const spX = i => +(i / Math.max(1, N - 1) * SPARK_W).toFixed(1);
const spY = (v, hi) => +(SPARK_H - 2 - Math.min(1, v / hi) * (SPARK_H - 4)).toFixed(1);
// The top of a scale: the largest value, or a high percentile when one huge merge should not flatten the rest.
const scaleTop = (get, p = 1) => { const s = R.map(get).filter(v => v > 0).sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor(p * s.length))] || 1; };
// A line over every merge, broken where a merge has no value, so a gap shows as a gap.
const linePath = (get, hi) => R.map((r, i) => get(r) == null ? "" : `${i && get(R[i - 1]) != null ? "L" : "M"}${spX(i)},${spY(get(r), hi)}`).join("");
// One bar per merge, up from the baseline; a value past the top is clipped.
const barPath = (get, hi) => R.map((r, i) => get(r) ? `M${spX(i)},${SPARK_H - 1}V${spY(get(r), hi)}` : "").join("");
const specTotal = r => Object.values(r.spec_files || {}).reduce((a, b) => a + b, 0);
const SPARK_SERIES = {
  names: [{ get: r => r.name_coverage, hi: 1, color: "var(--code)" }, { get: r => r.jaccard, hi: 1, color: "var(--muted)", dash: true }],
  prod: [{ get: r => r.prod_name_coverage, hi: 1, color: "var(--code)" }],
  kept: [{ get: r => r.names_kept, hi: scaleTop(r => r.names_kept), color: "var(--code)" }],
  retired: [{ get: r => r.names_retired, hi: scaleTop(r => r.names_retired), color: "var(--code)" }],
  gap: [{ get: r => r.gap_full, hi: scaleTop(r => r.gap_full), color: "var(--gap)" }],
  coverage: [{ get: r => r.coverage, hi: 1, color: "var(--code)" }],
  code: [{ get: r => r.code_churn, hi: scaleTop(r => r.code_churn, 0.95), color: "var(--code)", bars: true }],
  spec: [{ get: specTotal, hi: scaleTop(specTotal, 0.95), color: "var(--spec)", bars: true }],
};
// The paths are made once; a redraw only adds the dot at the selected merge.
const SPARK = Object.fromEntries(Object.entries(SPARK_SERIES).map(([k, ss]) =>
  [k, ss.map(s => ({ ...s, d: s.bars ? barPath(s.get, s.hi) : linePath(s.get, s.hi) }))]));
const sparkHtml = k => `<svg viewBox="0 0 ${SPARK_W} ${SPARK_H}" preserveAspectRatio="none" role="img" aria-label="Trend across merges, dot at the selected merge">`
  + SPARK[k].map(s => {
    const v = s.get(R[sel]);
    // The dot is a zero-length round stroke, not a circle: it stays round however the svg is stretched.
    return `<path d="${s.d}" fill="none" stroke="${s.color}" stroke-width="1.25" vector-effect="non-scaling-stroke"${s.dash ? ' stroke-dasharray="3 2"' : ""}/>`
      + (v == null ? "" : `<path d="M${spX(sel)},${spY(v, s.hi)}h0" stroke="var(--ink)" stroke-width="5" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`);
  }).join("") + "</svg>";

// A name share with its counts; with no spec name there is nothing to divide, which is not 0% or 100%.
// Data from before the counts were kept has no spec_names: the share alone, or – without one.
const nameShare = (x, n, d) => x == null ? (d === 0 ? "not measured: no extracted spec names" : "–")
  : `${fmt(x * 100, 1)}%` + (d == null ? "" : ` <small>${n}/${d}</small>`);
// The names behind the two shares: each found only outside production with its places and paths, then the missing.
function nameLists(s) {
  if (s.names_outside_prod == null) return "";  // data from before the lists were kept
  const out = Object.entries(s.names_outside_prod), absent = s.names_absent || [];
  return `<details class="names"><summary>Present only outside production <span class="num">(${out.length})</span></summary>
      ${out.length ? `<ul>${out.map(([n, at]) => `<li><code>${esc(n)}</code> ${Object.entries(at).map(([p, fs]) =>
        `<span class="chip">${esc(p)}</span> ${fs.map(f => `<code class="path">${esc(f)}</code>`).join(" ")}`).join(" ")}</li>`).join("")}</ul>` : "<p>None.</p>"}
    </details>
    <details class="names"><summary>Absent from all scanned code <span class="num">(${absent.length})</span></summary>
      ${absent.length ? `<ul>${absent.map(n => `<li><code>${esc(n)}</code></li>`).join("")}</ul>` : "<p>None.</p>"}
    </details>`;
}

function drawReadout() {
  const s = R[sel], prev = R[Math.max(0, sel - 1)];
  const dGap = s.gap_full - prev.gap_full, specLines = Object.values(s.spec_files || {}).reduce((a, b) => a + b, 0);
  const sign = x => (x > 0 ? "+" : x < 0 ? "−" : "±") + Math.abs(x).toFixed(2);
  const edits = Object.entries(s.spec_files || {}).sort((a, b) => b[1] - a[1]);
  const moved = sel === 0 ? "Starting point: the code and the plan written for it." :
    !specLines && Math.abs(dGap) < 0.05 ? "Neither the spec nor the gap moved noticeably." :
    !specLines ? `Only the code moved. The gap ${dGap < 0 ? "narrowed" : "widened"} by ${Math.abs(dGap).toFixed(2)}°.` :
    `${specLines} spec lines changed and the gap ${dGap < 0 ? "narrowed" : "widened"} by ${Math.abs(dGap).toFixed(2)}°.`;
  document.getElementById("readout").innerHTML = `
    <div class="eyebrow">Selected merge</div>
    <div class="title">${prLabel(s)} · ${esc(s.title)}</div>
    <div><span class="chip" style="color:${KVAR[s.kind]}">${KIND[s.kind]}</span> <span class="num" style="color:var(--muted);font-size:12.5px;margin-left:6px">${s.sha} · ${s.date}</span></div>
    <p style="font-size:14px;color:var(--ink-2)">${moved}</p>
    <div class="hr"></div>
    <dl>
      <dt>Spec names in code, all places</dt><dd>${nameShare(s.name_coverage, s.names_in_code, s.spec_names)}</dd><dd class="spark">${sparkHtml("names")}</dd>
      <dt>Spec names in production</dt><dd>${nameShare(s.prod_name_coverage, s.names_in_prod, s.spec_names)}</dd><dd class="spark">${sparkHtml("prod")}</dd>
      <dt>Jaccard, spec ∩ code</dt><dd>${s.jaccard == null ? "–" : fmt(s.jaccard, 2)}</dd><dd></dd>
      <dt>Names the specs dropped, still in code</dt><dd>${s.names_kept ?? "–"}</dd><dd class="spark">${sparkHtml("kept")}</dd>
      <dt>Spec names retired from the code</dt><dd>${s.names_retired ?? "–"}</dd><dd class="spark">${sparkHtml("retired")}</dd>
      <dt>Spec–code name-gap angle (secondary)</dt><dd>${fmt(s.gap_full, 2)}° <span style="color:var(--muted)">(${sign(dGap)})</span></dd><dd class="spark">${sparkHtml("gap")}</dd>
      <dt>Against shuffled counts (95%: ${band(s.gap_chance_band)})</dt><dd>${bandPlace(s)}</dd><dd></dd>
      <dt>Reached-day names in code</dt><dd>${s.coverage == null ? "–" : fmt(s.coverage * 100, 1) + "%"}</dd><dd class="spark">${sparkHtml("coverage")}</dd>
      <dt>Code lines changed</dt><dd>${s.code_churn.toLocaleString()}</dd><dd class="spark">${sparkHtml("code")}</dd>
      <dt>Spec lines changed</dt><dd>${edits.reduce((a, b) => a + b[1], 0)}</dd><dd class="spark">${sparkHtml("spec")}</dd>
    </dl>
    <p class="limit">Vocabulary: matches extracted names, not behavior. Test and tool names may legitimately be absent from production.
      Gap: compared with seeded shuffles of name counts; this is not a correctness test.</p>
    ${nameLists(s)}
    ${edits.length ? `<div style="font-size:12.5px;color:var(--ink-2)">${edits.slice(0, 5).map(([f, n]) => `<div style="display:flex;justify-content:space-between;gap:10px"><code>${esc(f.replace("specs/", ""))}</code><span class="num">${n}</span></div>`).join("")}${edits.length > 5 ? `<div style="color:var(--muted)">+${edits.length - 5} more files</div>` : ""}</div>` : ""}`;
}

/* ---------- line charts ---------- */
const LW = 1000, LP = { l: 44, r: 104, t: 14, b: 60 };
// A lone row (no merge yet) sits mid-plot, and its strip cell, i ± 0.5, spans the plot.
const lx = i => LP.l + (N > 1 ? i / (N - 1) : i + 0.5) * (LW - LP.l - LP.r);
function dayStarts() {
  const out = []; let d = -1;
  R.forEach((r, i) => { if (r.day !== d) { out.push([i, r.day]); d = r.day; } });
  return out;
}
// The first merge of each calendar day: the x axis is merges, so a busy date takes more width.
function dateStarts() {
  return R.map((r, i) => [i, r.date]).filter(([i, s]) => !i || R[i - 1].date !== s);
}
const steps = (lo, hi, n) => Array.from({ length: n + 1 }, (_, i) => +(lo + (hi - lo) * i / n).toFixed(1));
// The out-of-order days that have a merge: one not run yet has no day label for a * to mark.
const oooRun = () => [...OOO].filter(d => R.some(r => r.day === d)).sort((a, b) => rank(a) - rank(b));
// The key to the * on a day label, under any axis that draws day labels; there is none when no day ran out of order.
function oooNote(box) {
  const days = oooRun();
  if (!days.length) return;
  box.insertAdjacentHTML("beforeend", `<p class="axis-note">* Day run out of number order: ${days.map(d => "D" + dd(d)).join(", ")}.</p>`);
}
// bands: [{ color, get: r => [lo, hi] }], shaded under the lines.
// empty: what the chart says instead, when no solid line has a point yet.
function lineChart(box, { H, yMin, yMax, ticks, unit, series, bands, label, strip, digits = 1, empty }) {
  box.innerHTML = "";
  if (empty && !series.some(s => !s.dash && R.some(r => s.get(r) != null))) { box.innerHTML = `<p class="load-error">${empty}</p>`; return; }
  const svg = el("svg", { viewBox: `0 0 ${LW} ${H}`, role: "img", "aria-label": label }, box);
  const ly = v => LP.t + (1 - (v - yMin) / (yMax - yMin)) * (H - LP.t - LP.b);
  ticks.forEach(t => {
    el("line", { x1: LP.l, x2: LW - LP.r, y1: ly(t), y2: ly(t), stroke: "var(--grid)" }, svg);
    el("text", { x: LP.l - 8, y: ly(t) + 4, "text-anchor": "end", class: "num" }, svg).textContent = t + unit;
  });
  el("line", { x1: LP.l, x2: LW - LP.r, y1: H - LP.b, y2: H - LP.b, stroke: "var(--axis)" }, svg);
  let lastX = -99;
  dayStarts().forEach(([i, d]) => {
    if (!d || lx(i) - lastX < 34) return;
    lastX = lx(i);
    el("line", { x1: lx(i), x2: lx(i), y1: LP.t, y2: H - LP.b, stroke: "var(--grid)", "stroke-dasharray": "2 3" }, svg);
    const t = el("text", { x: lx(i) + 3, y: H - LP.b + (strip ? 30 : 16), class: "num" + (OOO.has(d) ? " ooo" : "") }, svg);
    t.textContent = "D" + dd(d) + (OOO.has(d) ? "*" : "");
  });
  let lastDate = -99;
  dateStarts().forEach(([i, s]) => {
    el("line", { x1: lx(i), x2: lx(i), y1: H - LP.b, y2: H - LP.b + (strip ? 36 : 22), stroke: "var(--axis)" }, svg);
    if (lx(i) - lastDate < 44) return;
    lastDate = lx(i);
    el("text", { x: lx(i) + 3, y: H - LP.b + (strip ? 46 : 32), class: "date" }, svg).textContent = shortDate(s);
  });
  if (strip) {
    // A merge's cell runs halfway to each neighbour, clipped to the plot: with few merges a cell is wide, and the end ones ran off the card.
    const edge = x => Math.min(LW - LP.r, Math.max(LP.l, x));
    R.forEach((r, i) => {
      const a = edge(lx(i - 0.5)), b = edge(lx(i + 0.5));
      el("rect", { x: a + 1, y: H - LP.b + 5, width: Math.max(2, b - a - 2), height: 8, rx: 2, fill: KVAR[r.kind] }, svg);
    });
  }
  el("line", { x1: lx(sel), x2: lx(sel), y1: LP.t, y2: H - LP.b, stroke: "var(--ink-2)", "stroke-width": 1 }, svg);
  const cross = el("line", { x1: 0, x2: 0, y1: LP.t, y2: H - LP.b, stroke: "var(--muted)", "stroke-dasharray": "3 3", visibility: "hidden" }, svg);
  (bands || []).forEach(b => {
    const pts = R.map((r, i) => [i, b.get(r)]).filter(p => p[1] != null);
    if (!pts.length) return;
    const edge = k => pts.map(([i, v]) => `${lx(i)},${ly(v[k])}`);
    el("polygon", { points: [...edge(0), ...edge(1).reverse()].join(" "), fill: b.color, "fill-opacity": 0.12, stroke: "none" }, svg);
  });
  series.forEach(s => {
    const pts = R.map((r, i) => [i, s.get(r)]).filter(p => p[1] != null);
    if (!pts.length) return;
    el("polyline", { points: pts.map(([i, v]) => `${lx(i)},${ly(v)}`).join(" "), fill: "none", stroke: s.color, "stroke-width": s.dash ? 1.5 : 2, "stroke-linejoin": "round", "stroke-linecap": "round", ...(s.dash ? { "stroke-dasharray": "5 4" } : {}) }, svg);
    const v = s.get(R[sel]);
    if (v != null && !s.dash) el("circle", { cx: lx(sel), cy: ly(v), r: 5, fill: s.color, stroke: "var(--surface)", "stroke-width": 2 }, svg);
    const end = pts[pts.length - 1];
    el("text", { x: lx(end[0]) + 10, y: ly(end[1]) + 4 + (s.dy || 0), class: "lbl" }, svg).textContent = s.end || `${s.name} ${fmt(end[1], digits)}${unit}`;
  });
  const hit = el("rect", { x: LP.l - 6, y: 0, width: LW - LP.l - LP.r + 12, height: H, class: "hit" }, svg);
  const idxAt = ev => {
    const p = svg.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY;
    const q = p.matrixTransform(svg.getScreenCTM().inverse());
    return Math.max(0, Math.min(N - 1, Math.round((q.x - LP.l) / (LW - LP.l - LP.r) * (N - 1))));
  };
  hit.addEventListener("mousemove", ev => { const i = idxAt(ev); cross.setAttribute("x1", lx(i)); cross.setAttribute("x2", lx(i)); cross.setAttribute("visibility", "visible"); showTip(ev, rowTip(R[i])); });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
  hit.addEventListener("click", ev => setSel(idxAt(ev)));
  oooNote(box);
}
// The axes follow the data, so a later phase that pushes the gap or coverage past today's range stays on the chart.
// At least one 15° step: with no gap yet the axis was 0 to 0, its ticks 0/0, and drew "NaN°".
const degMax = Math.max(15, Math.ceil(Math.max(...R.map(r => Math.max(r.gap_full || 0, (r.gap_chance_band || [])[1] || 0))) / 15) * 15);
const covMin = Math.min(92, Math.floor(Math.min(...R.filter(r => r.coverage != null).map(r => r.coverage * 100)) / 2) * 2);
function drawLines() {
  // The legend's sample is this repository's first out-of-order day, and there is no key when none ran out of order.
  const key = document.getElementById("ooo-key"), first = oooRun()[0];
  if (key) { if (first == null) key.style.display = "none"; else key.querySelector("b").textContent = "D" + dd(first) + "*"; }
  lineChart(document.getElementById("lines"), {
    H: 330, yMin: 0, yMax: degMax, ticks: steps(0, degMax, degMax / 15), unit: "°", strip: true,
    label: "Spec–code name gap in degrees across merges",
    series: [
      { name: "gap", color: "var(--gap)", get: r => r.gap_full },
      { name: "chance", color: "var(--gap)", dash: true, get: r => r.gap_chance ?? null },
    ],
    bands: [{ color: "var(--gap)", get: r => r.gap_chance_band ?? null }],
  });
  lineChart(document.getElementById("cover"), {
    H: 190, yMin: covMin, yMax: 100, ticks: steps(covMin, 100, 4), unit: "%", strip: false,
    label: "Share of reached-day spec names present in code",
    series: [{ name: "in code", color: "var(--code)", get: r => r.coverage == null ? null : +(r.coverage * 100).toFixed(2) }],
  });
  lineChart(document.getElementById("evidence-history"), {
    H: 190, yMin: 0, yMax: evMax, ticks: steps(0, evMax, 4), unit: "", strip: false, digits: 0,
    label: "Tests named by finished days: present on each merge, not running (orange), retired (grey)",
    series: [
      { name: "present", color: "var(--close)", get: r => r.evidence && r.evidence.present },
      { name: "not running", color: "var(--code)", dy: 8, get: r => r.evidence && r.evidence.skippable + r.evidence.missing },
      { name: "retired", color: "var(--muted)", dy: -7, get: r => r.evidence && (r.evidence.retired || 0) },
    ],
  });
  lineChart(document.getElementById("moved"), {
    H: 190, yMin: 0, yMax: 100, ticks: steps(0, 100, 4), unit: "%", strip: false,
    label: "Share of the services' main source lines in their own directories",
    empty: "No service code yet: the share counts the packages under <code>services</code> in <code>spec-drift.config.json</code>, and the code has none.",
    series: [{ name: "out", color: "var(--close)", get: r => movedShare(r) }],
  });
  lineChart(document.getElementById("churn"), {
    H: 220, yMin: 0, yMax: 100, ticks: steps(0, 100, 4), unit: "%", strip: true,
    label: "Spec and code lines changed so far, each as a share of its own total",
    series: [
      { name: "code", color: "var(--code)", end: "code", dy: -7, get: r => cumShare(r, "code") },
      { name: "spec", color: "var(--spec)", end: "spec", dy: 8, get: r => cumShare(r, "spec") },
    ],
  });
  lineChart(document.getElementById("test-ratio"), {
    H: 200, yMin: 0, yMax: ratioMax, ticks: steps(0, ratioMax, ratioMax <= 6 ? ratioMax : 4),
    unit: "×", strip: false, digits: 2,
    empty: `No ratio yet: it starts once ${RATIO_FROM.toLocaleString("en")} production lines have changed; ${(WORK.get(last).prod || 0).toLocaleString("en")} so far.`,
    label: "Test lines changed so far per production line changed so far",
    series: [
      { name: "one to one", color: "var(--muted)", dash: true, end: "1 : 1", get: () => 1 },
      { name: "tests", color: "var(--close)", get: r => testRatio(r) },
    ],
  });
  const p = R[sel].placed || {};
  document.getElementById("placed").innerHTML = `<p>At ${prLabel(R[sel])}: ` + (Object.keys(p).sort().map(s =>
    `<b>${s}</b> ${p[s][1] ? (p[s][0] ? `${p[s][1]} lines out, ${p[s][0]} still in place` : `out (${p[s][1]} lines)`) : `in place (${p[s][0]} lines)`}`).join(" · ")
    || "no service code.") + "</p>";
}
const movedShare = r => {
  const v = Object.values(r.placed || {}), all = v.reduce((a, [i, o]) => a + i + o, 0);
  return all ? +(v.reduce((a, [, o]) => a + o, 0) / all * 100).toFixed(1) : null;
};
const evMax = Math.max(4, Math.ceil(Math.max(0, ...R.map(r => r.evidence ? r.evidence.present : 0)) / 20) * 20);
// Code lines outnumber spec lines several times over, so each runs to 100% of its own total rather than sharing a scale.
const CUM = new Map();
R.reduce(([s, c], r) => {
  const next = [s + Object.values(r.spec_files || {}).reduce((a, b) => a + b, 0), c + (r.code_churn || 0)];
  CUM.set(r, next); return next;
}, [0, 0]);
const cumShare = (r, k) => { const [s, c] = CUM.get(r), [S, C] = CUM.get(last); return k === "spec" ? (S ? s / S * 100 : 0) : (C ? c / C * 100 : 0); };
// Lines per box, summed merge by merge; the ratio waits for 1,000 production lines, so tests written before any code do not read as a spike.
const WORK = new Map();
R.reduce((acc, r) => {
  const next = { ...acc };
  Object.entries(r.work || {}).forEach(([k, v]) => { next[k] = (next[k] || 0) + v; });
  WORK.set(r, next); return next;
}, {});
const RATIO_FROM = 1000;
const testRatio = r => { const w = WORK.get(r); return w.prod >= RATIO_FROM ? +(w.test / w.prod).toFixed(2) : null; };
const ratioMax = Math.max(2, Math.ceil(Math.max(0, ...R.map(testRatio).filter(v => v != null))));
// One bar, split into boxes in order; a box's share is printed inside it when there is room.
function shareBar(box, parts, label) {
  box.innerHTML = "";
  const H = 40, total = parts.reduce((a, p) => a + p.value, 0) || 1, P = { l: 0, r: 0 };
  const svg = el("svg", { viewBox: `0 0 ${LW} ${H}`, role: "img", "aria-label": label }, box);
  let x = P.l;
  parts.forEach(p => {
    const w = p.value / total * (LW - P.l - P.r);
    if (!w) return;
    const rect = el("rect", { x: x + 1, y: 4, width: Math.max(1, w - 2), height: H - 8, rx: 3, fill: p.color, ...(p.opacity == null ? {} : { opacity: p.opacity }) }, svg);
    if (w > 54) el("text", { x: x + 10, y: H / 2 + 5, class: "num", style: "fill:var(--surface);font-weight:600" }, svg).textContent = fmt(p.value / total * 100, 0) + "%";
    rect.addEventListener("mousemove", ev => showTip(ev, `<b>${esc(p.name)}</b>${p.tip || ""}<div class="row"><span>share</span><span>${fmt(p.value / total * 100, 1)}%</span></div>`));
    rect.addEventListener("mouseleave", hideTip);
    x += w;
  });
}
function drawWork() {
  const w = WORK.get(last), n = k => w[k] || 0;
  const row = (k, v) => `<div class="row"><span>${k}</span><span>${v.toLocaleString()}</span></div>`;
  const parts = [
    { name: "Specs", value: n("spec"), color: "var(--spec)", tip: row("plan.md and specs/", n("spec")) },
    { name: "Context", value: n("context"), color: "var(--w-context)", tip: row("CLAUDE.md, agents, README, docs", n("context")) },
    { name: "Production code", value: n("prod"), color: "var(--code)", tip: row("code and config", n("prod")) },
    { name: "Tests and verification", value: n("test") + n("tooling"), color: "var(--close)",
      tip: row("tests", n("test")) + row("scripts, CI, dashboard", n("tooling")) },
  ];
  shareBar(document.getElementById("work-bar"), parts, "Lines changed in merged pull requests, by what the file is");
  document.getElementById("work-legend").innerHTML = parts.map(p =>
    `<span><i class="dot" style="background:${p.color}"></i>${p.name} · ${kilo(p.value)}</span>`).join("")
    + `<span>${fmt(n("test") / Math.max(1, n("prod")), 1)} test lines per production line</span>`;
}
const gapDelta = i => step(R[i - 1].gap_full, R[i].gap_full);
const signed = (x, d = 2) => (x > 0 ? "+" : x < 0 ? "−" : "") + fmt(Math.abs(x), d);

/* ---------- per-merge change in gap ---------- */
// A panel on the merge axis of the line chart above: the step each merge took on the gap.
function drawDeltas() {
  const box = document.getElementById("deltas");
  box.innerHTML = "";
  const panels = [
    { name: "gap", label: "Δ gap", get: r => r.gap_full },
  ];
  const PH = 200, GAP = 18, H = LP.t + panels.length * PH + (panels.length - 1) * GAP + 40;
  const svg = el("svg", { viewBox: `0 0 ${LW} ${H}`, role: "img", "aria-label": "Change in gap at each merge, in merge order" }, box);
  panels.forEach((pn, k) => {
    const top = LP.t + k * (PH + GAP), bot = top + PH;
    const pts = R.slice(1).map((r, j) => ({ i: j + 1, r, d: step(pn.get(R[j]), pn.get(r)) }));
    const lo = Math.min(0, ...pts.map(p => p.d)), hi = Math.max(0, ...pts.map(p => p.d));
    const unit = hi - lo > 4 ? 1 : 0.5, yMin = Math.floor(lo / unit) * unit, yMax = Math.max(Math.ceil(hi / unit) * unit, yMin + unit);
    const ly = v => top + (1 - (v - yMin) / (yMax - yMin)) * PH;
    for (let t = yMin; t <= yMax + 1e-9; t += unit) {
      el("line", { x1: LP.l, x2: LW - LP.r, y1: ly(t), y2: ly(t), stroke: Math.abs(t) < 1e-9 ? "var(--axis)" : "var(--grid)" }, svg);
      el("text", { x: LP.l - 8, y: ly(t) + 4, "text-anchor": "end", class: "num" }, svg).textContent = signed(t, t % 1 ? 1 : 0) + "°";
    }
    el("rect", { x: LP.l, y: top, width: LW - LP.l - LP.r, height: PH, fill: "none", stroke: "var(--axis)" }, svg);
    el("text", { x: LW - LP.r + 10, y: top + 16, class: "lbl" }, svg).textContent = pn.label;
    const net = pts.reduce((a, p) => a + p.d, 0);
    el("text", { x: LW - LP.r + 10, y: top + 34, class: "num" }, svg).textContent = `net ${signed(net, 1)}°`;
    let lastX = -99;
    dayStarts().forEach(([i, d]) => {
      if (!d || lx(i) - lastX < 34) return;
      lastX = lx(i);
      el("line", { x1: lx(i), x2: lx(i), y1: top, y2: bot, stroke: "var(--grid)", "stroke-dasharray": "2 3" }, svg);
      if (k === panels.length - 1) {
        const t = el("text", { x: lx(i) + 3, y: bot + 16, class: "num" + (OOO.has(d) ? " ooo" : "") }, svg);
        t.textContent = "D" + dd(d) + (OOO.has(d) ? "*" : "");
      }
    });
    el("line", { x1: lx(sel), x2: lx(sel), y1: top, y2: bot, stroke: "var(--ink-2)", "stroke-width": 1 }, svg);
    pts.forEach(p => {
      el("circle", { cx: lx(p.i), cy: ly(p.d), r: 3.5, fill: KVAR[p.r.kind], stroke: "var(--surface)", "stroke-width": 1 }, svg);
      if (p.i === sel) el("circle", { cx: lx(p.i), cy: ly(p.d), r: 7, fill: "none", stroke: "var(--ink)", "stroke-width": 1.5 }, svg);
      const hit = el("circle", { cx: lx(p.i), cy: ly(p.d), r: 7, class: "hit" }, svg);
      hit.addEventListener("mousemove", ev => showTip(ev, rowTip(p.r) + `<div class="row"><span>change in ${pn.name}</span><span>${signed(p.d)}°</span></div>`));
      hit.addEventListener("mouseleave", hideTip);
      hit.addEventListener("click", () => setSel(p.i));
    });
  });
  let lastDate = -99;
  const bot = H - 40 + 4;
  dateStarts().forEach(([i, s]) => {
    if (lx(i) - lastDate < 44) return;
    lastDate = lx(i);
    el("text", { x: lx(i) + 3, y: bot + 28, class: "date" }, svg).textContent = shortDate(s);
  });
  oooNote(box);
}

/* ---------- pull requests per day ---------- */
function drawPerDay() {
  const [S, C] = CUM.get(last);
  document.getElementById("churn-legend").innerHTML = `<span><i style="background:var(--spec)"></i>Spec lines changed · ${kilo(S)}</span>
    <span><i style="background:var(--code)"></i>Code lines changed · ${kilo(C)}</span>`;
  const merges = R.filter(r => r.pr);
  const totals = ["spec", "code", "close"].map(k => ({ name: KIND[k], value: merges.filter(r => r.kind === k).length, color: KVAR[k] }));
  const groups = DATA.other_groups;
  if (groups) {
    const opacity = [1, 0.75, 0.55, 0.4];
    groups.forEach((g, i) => totals.push({ name: g.name, value: g.count, color: "var(--other)", opacity: opacity[i] }));
  } else {
    totals.push({ name: KIND.other, value: merges.filter(r => r.kind === "other").length, color: KVAR.other });
  }
  shareBar(document.getElementById("pr-total"), totals, "Every merged pull request by kind");
  document.getElementById("pr-total-legend").innerHTML = totals.map(t =>
    `<span><i class="dot" style="background:${t.color}${t.opacity == null ? "" : `;opacity:${t.opacity}`}"></i>${esc(t.name)} · ${t.value}</span>`).join("") + `<span>${merges.length} in all</span>`;
  const box = document.getElementById("perday");
  box.innerHTML = "";
  const kinds = ["spec", "code", "close", "other"], H = 240, P = { l: 44, r: 16, t: 22, b: 30 };
  const days = DATA.days.filter(d => d.prs.length).sort((a, b) => rank(a.day) - rank(b.day));
  if (!days.length) { box.innerHTML = '<p class="load-error">No day has a merged pull request yet: a PR counts for a day when its branch is <code>day-NN/…</code>.</p>'; return; }
  const count = (d, k) => d.prs.filter(p => p.kind === k).length;
  const yMax = Math.max(4, Math.ceil(Math.max(...days.map(d => d.prs.length)) / 4) * 4);
  const ly = v => P.t + (1 - v / yMax) * (H - P.t - P.b);
  const bw = (LW - P.l - P.r) / days.length, w = Math.min(28, bw - 6);
  const svg = el("svg", { viewBox: `0 0 ${LW} ${H}`, role: "img", "aria-label": "Pull requests per day by kind, in run order" }, box);
  steps(0, yMax, 4).forEach(t => {
    el("line", { x1: P.l, x2: LW - P.r, y1: ly(t), y2: ly(t), stroke: t ? "var(--grid)" : "var(--axis)" }, svg);
    el("text", { x: P.l - 8, y: ly(t) + 4, "text-anchor": "end", class: "num" }, svg).textContent = t;
  });
  days.forEach((d, j) => {
    const x = P.l + j * bw + (bw - w) / 2;
    let base = 0;
    kinds.forEach(k => {
      const n = count(d, k);
      if (!n) return;
      // A 2px gap of surface between segments, taken from the top of each.
      el("rect", { x, y: ly(base + n), width: w, height: Math.max(1, ly(base) - ly(base + n) - 2), rx: 2, fill: KVAR[k] }, svg);
      base += n;
    });
    el("text", { x: x + w / 2, y: ly(base) - 6, "text-anchor": "middle", class: "num" }, svg).textContent = base;
    el("text", { x: x + w / 2, y: H - P.b + 16, "text-anchor": "middle", class: "num" + (OOO.has(d.day) ? " ooo" : "") }, svg)
      .textContent = "D" + dd(d.day) + (OOO.has(d.day) ? "*" : "");
    const hit = el("rect", { x: P.l + j * bw, y: P.t, width: bw, height: H - P.t - P.b, class: "hit" }, svg);
    hit.addEventListener("mousemove", ev => showTip(ev, `<b>Day ${dd(d.day)}</b><div style="margin:3px 0 6px">${esc(d.title)}</div>`
      + kinds.map(k => `<div class="row"><span>${KIND[k]}</span><span>${count(d, k)}</span></div>`).join("")
      + `<div class="row"><span>expected PRs, first written</span><span>${d.expected_first ?? "–"}</span></div>`
      + `<div class="row"><span>expected PRs, last written</span><span>${d.expected ?? "–"}</span></div>`));
    hit.addEventListener("mouseleave", hideTip);
    hit.addEventListener("click", () => {
      const nums = new Set(d.prs.map(p => p.number)), i = R.map(r => nums.has(r.pr)).lastIndexOf(true);
      if (i > 0) setSel(i);
    });
  });
  oooNote(box);
}

/* ---------- verification that leaves no lines ---------- */
function drawVerification() {
  const V = DATA.verification || [], code = V.filter(v => v.kind === "code");
  if (!V.length) return;
  const n = (vs, s) => vs.filter(v => v.breaks === s).length;
  // From this PR the rules ask for the break line, so only those PRs can be silent; 0 is every PR.
  const from = (DATA.meta || {}).break_line_from_pr || 0;
  const since = code.filter(v => v.breaks !== "not measured"), silent = since.filter(v => v.breaks === "silent");
  const recorded = n(since, "recorded"), none = n(since, "none");
  document.getElementById("breaks").innerHTML = `<p>${from ? `Since #${from}, ` : ""}${recorded} of ${since.length} code PRs record a break on purpose${none ? `, ${none} say why there is none` : ""}; ${silent.length} silent.</p>`
    + (from ? `<p style="font-size:13px;color:var(--muted)">${n(code, "not measured")} code PRs before #${from} are not measured.</p>` : "")
    + (silent.length ? `<ul>${silent.map(v => `<li>#${v.number}${v.day ? ` · Day ${dd(v.day)}` : ""}</li>`).join("")}</ul>` : "");
  const sum = (vs, k) => vs.reduce((a, v) => a + v[k], 0);
  const pushes = sum(V, "pushes"), failed = sum(V, "failed"), bad = V.filter(v => v.failed);
  document.getElementById("ci-legend").textContent = `${failed} of ${pushes} pushes CI ran on failed (${fmt(failed / Math.max(1, pushes) * 100, 1)}%), in ${bad.length} of ${V.length} merged PRs.`;
  document.getElementById("ci-fails").innerHTML = `<thead><tr><th>PR</th><th>Day</th><th class="r">Failed pushes</th><th>Workflows that failed</th></tr></thead><tbody>` +
    bad.map(v => `<tr><td class="num">#${v.number}</td><td class="num">${v.day ? dd(v.day) : "–"}</td><td class="r num">${v.failed} of ${v.pushes}</td><td>${esc(v.failed_in.join(", "))}</td></tr>`).join("") + "</tbody>";
}

/* ---------- heatmap ---------- */
function drawHeat() {
  const box = document.getElementById("heat");
  box.innerHTML = "";
  const files = DATA.files, cols = R.slice(1);
  const CW = 15, CH = 13, L = 96, T = 30;
  const W = L + cols.length * CW + 8, H = T + files.length * CH + 34;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Lines edited per spec file per merge", style: `min-width:${W}px;max-width:${W}px` }, box);
  const max = Math.max(...cols.flatMap(r => Object.values(r.spec_files || {})), 1);
  files.forEach((f, j) => {
    const d = dayOfFile(f), twin = files.filter(g => dayOfFile(g) === d).length > 1;
    el("text", { x: L - 8, y: T + j * CH + CH - 3, "text-anchor": "end", class: "num", style: "font-size:10.5px" }, svg)
      .textContent = f === "plan.md" ? "plan.md" : "day " + dd(d) + (twin ? " " + f.split("-")[2] : "");
  });
  cols.forEach((r, c) => {
    const x = L + c * CW;
    el("rect", { x: x + 1, y: 8, width: CW - 2, height: 8, rx: 2, fill: KVAR[r.kind] }, svg);
    files.forEach((f, j) => {
      const n = (r.spec_files || {})[f] || 0;
      const pct = n ? Math.round(22 + 78 * Math.sqrt(n / max)) : 0;
      el("rect", { x: x + 1, y: T + j * CH + 1, width: CW - 2, height: CH - 2, rx: 2,
        fill: n ? `color-mix(in oklab, var(--spec) ${pct}%, var(--surface))` : "var(--surface-2)" }, svg);
    });
    if (c % 5 === 0 || c === cols.length - 1)
      el("text", { x: x + CW / 2, y: H - 12, "text-anchor": "middle", class: "num", style: "font-size:10px" }, svg).textContent = "#" + r.pr;
  });
  // frontier: bottom edge of the day being worked on
  const rowOfDay = d => files.findIndex(f => dayOfFile(f) === d);
  let path = "";
  cols.forEach((r, c) => {
    const y = T + (rowOfDay(Math.max(r.day, 1)) + 1) * CH;
    path += (c === 0 ? `M${L + c * CW},${y}` : `V${y}`) + `H${L + (c + 1) * CW}`;
  });
  el("path", { d: path, fill: "none", stroke: "var(--code)", "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  if (sel > 0) el("rect", { x: L + (sel - 1) * CW + 0.5, y: 4, width: CW - 1, height: T + files.length * CH - 2, rx: 3, fill: "none", stroke: "var(--ink)", "stroke-width": 1.5 }, svg);
  const hit = el("rect", { x: L, y: 0, width: cols.length * CW, height: T + files.length * CH, class: "hit" }, svg);
  const at = ev => {
    const p = svg.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY;
    const q = p.matrixTransform(svg.getScreenCTM().inverse());
    return [Math.floor((q.x - L) / CW), Math.floor((q.y - T) / CH)];
  };
  hit.addEventListener("mousemove", ev => {
    const [c, j] = at(ev);
    if (c < 0 || c >= cols.length) return hideTip();
    const r = cols[c];
    if (j < 0 || j >= files.length) return showTip(ev, rowTip(r));
    const f = files[j], n = (r.spec_files || {})[f] || 0, d = dayOfFile(f);
    const where = !d ? "the plan" : rank(d) > rank(r.day) ? `a future day (working on day ${r.day})` : d === r.day ? "the current day" : "a finished day";
    showTip(ev, `<b>${prLabel(r)} → ${esc(f.replace("specs/", ""))}</b><div style="margin:3px 0 6px">${esc(r.title)}</div>
      <div class="row"><span>lines edited</span><span>${n}</span></div><div class="row"><span>target</span><span>${where}</span></div>`);
  });
  hit.addEventListener("mouseleave", hideTip);
  hit.addEventListener("click", ev => { const [c] = at(ev); if (c >= 0 && c < cols.length) setSel(c + 1); });
  el("text", { x: L, y: H - 1, class: "lbl", style: "font-size:11px" }, svg).textContent = "▬ orange step: the day being worked on · darker blue = more lines edited";
}

/* ---------- findings + table ---------- */
// Each heading is a claim, so each is chosen by the numbers it rests on rather than fixed.
function drawFindings() {
  const covFloor = Math.min(...R.filter(r => r.coverage != null).map(r => r.coverage)) * 100;
  const codeShare = -byKind.code / gapClosed;
  const days = DATA.days.filter(finished).length;
  const f = [
    [last.deleted < DATA.origin_lines / 2 ? "Most of the plan as written still stands" : "Most of the plan as written has been rewritten",
     `${many(days, "day")} in, ${Math.round(last.deleted / DATA.origin_lines * 100)}% of the ${shortDate(R[0].date)} spec's lines have been rewritten, and ${last.added} lines added.`],
    [!gapClosed ? "No gap has closed yet" : specShare >= codeShare ? "The spec moved toward the code, not the reverse" : "The code now closes more of the gap than the spec",
     `Of the ${fmt(gapClosed)}° of gap that closed, ${ofGap(byKind.spec)} came from ${many(nKind("spec"), "spec-change PR")} and ${ofGap(byKind.code)} from ${many(nKind("code"), "code PR")}.`],
    // With no day reached there is no coverage to fall: Math.min() of nothing is Infinity.
    isFinite(covFloor) ? [covFloor >= 90 ? "Spec first keeps the gap from ever opening" : "The gap opened at least once",
     `Reached-day coverage never fell below ${fmt(covFloor, 1)}%: the share of names in the specs of days already reached that exist in the code.`]
      : ["No day reached yet", "Reached-day coverage, the share of names in the specs of days already reached that exist in the code, starts at the first merge that reaches a day."],
    [forward * 2 >= specEdits ? "Corrections reach forward" : "Corrections land on the day at hand",
     `${specEdits ? Math.round(forward / specEdits * 100) + "%" : "–"} of all edited day-spec lines (${forward} of ${specEdits}) went to days not reached yet.`],
  ];
  document.getElementById("findings").innerHTML = f.map(([h, p]) => `<div class="finding"><h3>${h}</h3><p>${p}</p></div>`).join("");
}
function drawTable() {
  document.getElementById("tbl").innerHTML = `<thead><tr><th>PR</th><th>Type</th><th>Day</th><th class="r">Name gap °</th><th class="r">Names in code</th><th class="r">Spec lines</th><th class="r">Code lines</th><th>Title</th></tr></thead><tbody>` +
    R.map((r, i) => `<tr data-i="${i}" class="${i === sel ? "sel" : ""}"><td class="num">${prLabel(r)}</td><td><span class="sw" style="background:${KVAR[r.kind]}"></span>${KIND[r.kind]}</td><td class="num">${r.day || "–"}</td><td class="r">${fmt(r.gap_full, 2)}</td><td class="r">${r.coverage == null ? "–" : fmt(r.coverage * 100, 1) + "%"}</td><td class="r">${Object.values(r.spec_files || {}).reduce((a, b) => a + b, 0)}</td><td class="r">${r.code_churn}</td><td class="t">${esc(r.title)}</td></tr>`).join("") + "</tbody>";
}
function drawScrubNow() {
  const s = R[sel];
  document.getElementById("scrub-now").innerHTML = `<span class="chip" style="color:${KVAR[s.kind]}">${KIND[s.kind]}</span>
    <span class="t">${prLabel(s)} · ${esc(s.title)}</span>
    <span class="num" style="color:var(--ink-2)">gap ${fmt(s.gap_full)}°</span>`;
}
// Each panel draws on its own: one that throws says so in its first box and the console, and the
// panels after it still draw.
function panel(id, draw) {
  try { draw(); } catch (e) {
    console.error(e);
    const box = document.getElementById(id), tag = box.tagName === "TABLE" ? "caption" : "p";
    box.innerHTML = `<${tag} class="load-error">This panel failed: ${esc(e && e.message || e)}</${tag}>`;
  }
}
function setSel(i) {
  sel = i; scrub.value = i;
  panel("scrub-now", drawScrubNow); panel("readout", drawReadout); panel("lines", drawLines); panel("deltas", drawDeltas); panel("heat", drawHeat);
  document.querySelectorAll("#tbl tr[data-i]").forEach(tr => tr.classList.toggle("sel", +tr.dataset.i === sel));
}

/* ---------- where we are ---------- */
// From spec-drift.py: the repository's name, plan.md's phase headings, and the days its list marks **Stop and evaluate.**
const PROJECT = (DATA.meta || {}).project || "";
document.title = `${PROJECT} Spec Drift`;
const PHASES = ((DATA.meta || {}).phases || []).map(esc);  // plan.md's text, which the template fills from <name>
const STOPS = (DATA.meta || {}).stops || [];
const STATUS = { done: "Done", closed: "Closed, boxes open", active: "In progress", ready: "Ready", provisional: "Provisional", cut: "Cut" };
const finished = d => d.status === "done" || d.status === "closed" || d.status === "cut";
function drawNow() {
  const now = DATA.now, days = DATA.days;
  const cur = days.find(d => d.day === now.current_day);
  const done = days.filter(finished).length;
  const open = now.open_prs.map(p => `<a href="${esc(p.url)}" target="_blank" rel="noopener">#${p.number}</a> ${esc(p.title)}`).join("<br>");
  const nextBox = `<div class="next"><span class="eyebrow">Next step</span><b>${md(now.next_step)}</b>${open ? `<div>${open}</div>` : ""}</div>
    <div class="refreshed">refreshed ${esc(now.generated.replace("T", " "))} · main at ${esc(now.head)}${now.last_pr ? ` (#${now.last_pr})` : ""}</div>`;
  // The days plan.md runs, and those with a spec it does not (cut): a new project writes specs one day at a time.
  const planned = new Set([...ORDER, ...days.map(d => d.day)]).size;
  // Merged PRs are the merges to main that name a PR; a direct commit is not one.
  document.getElementById("eyebrow").textContent = cur
    ? `${PROJECT} · Day ${dd(now.current_day)} of ${planned} · ${many(merged.length, "merged PR")}`
    : `${PROJECT} · between days · ${many(merged.length, "merged PR")}`;
  // Between days: the next piece of work has no day spec yet (the platform step, the cleanup day), or work has stopped.
  if (!cur) document.getElementById("today").innerHTML = `
    <div class="eyebrow">Between days</div>
    <div class="phase">${done} of ${days.length} days finished</div>${nextBox}`;
  else {
  const c = cur.criteria;
  const prs = cur.prs.length
    ? cur.prs.map(p => `<span class="chip" style="color:${KVAR[p.kind]}" title="${esc(p.title)}">#${p.number}</span>`).join(" ")
    : "none yet";
  document.getElementById("today").innerHTML = `
    <div class="eyebrow">Day ${dd(cur.day)} · ${STATUS[cur.status]}</div>
    <div class="day-title">${md(cur.title)}</div>
    <div class="phase">Phase ${cur.phase}: ${PHASES[cur.phase] || ""} · ${done} of ${days.length} days finished</div>
    <dl class="kv">
      <dt>Tracks</dt><dd><div class="trks">${cur.tracks.map(t => `<span class="trk ${cur.tracks_merged.includes(t) ? "done" : ""}" title="${esc(cur.track_work[t] || "")}">${t}</span>`).join("")}</div></dd>
      <dt>Criteria</dt><dd><span class="num">${c.ticked}/${c.total}</span> ticked · <span class="num">${c.new}</span> new · <span class="num">${c.hold}</span> hold</dd>
      <dt>Estimate</dt><dd><span class="num">${cur.expected_first ?? "–"} → ${cur.expected ?? "–"}</span> PRs, as first written → now</dd>
      <dt>Merged</dt><dd>${prs}</dd>
    </dl>
    ${nextBox}`;
  }
  // The next day plan.md names before its spec is written (the cleanup day), after the day it follows.
  const u = now.unwritten;
  const rows = PHASES.map((name, p) => {
    const ds = days.filter(d => d.phase === p);
    if (!ds.length) return "";
    const later = u && u.phase === p ? `<div class="tile unwritten" tabindex="0" aria-label="Day ${u.day}, spec not written">${dd(u.day)}</div>` : "";
    return `<div class="phase-row"><div class="phase-name"><b>${p}. ${name}</b>Days ${ds[0].day}–${later ? u.day : ds[ds.length - 1].day}</div><div class="tiles">${ds.map(d => {
      const share = d.tracks.length ? Math.min(1, d.tracks_merged.length / d.tracks.length) : 0;
      const bar = d.status === "active" ? `<span class="bar"><i style="width:${Math.round(share * 100)}%"></i></span>` : "";
      return `<div class="tile ${d.status}${STOPS.includes(d.day) ? " stop" : ""}${OOO.has(d.day) ? " ooo" : ""}" data-day="${d.day}" tabindex="0" aria-label="Day ${d.day}, ${STATUS[d.status]}">${dd(d.day)}${bar}</div>`;
    }).join("")}${later}</div></div>`;
  }).join("");
  document.getElementById("roadmap").innerHTML = rows + `<div class="tile-legend">
    <span><i class="tile done"></i>Done</span><span><i class="tile closed"></i>Closed, boxes open</span><span><i class="tile active"></i>In progress (bar: tracks merged)</span>
    <span><i class="tile"></i>Ready</span><span><i class="tile provisional"></i>Provisional</span><span><i class="tile cut"></i>Cut</span>${u ? `<span><i class="tile unwritten"></i>Spec not written</span>` : ""}
    <span><i class="tile stop"></i>Stop and evaluate</span><span><i class="tile ooo"></i>Run out of numeric order</span></div>`;
  document.querySelectorAll("#roadmap .tile.unwritten[tabindex]").forEach(tile => {
    const show = ev => showTip(ev, `<b>Day ${dd(u.day)} · Spec not written</b><div style="margin:3px 0 6px">${md(u.title)}</div>
      <div>${md(now.next_step)}</div>`);
    tile.addEventListener("mousemove", show);
    tile.addEventListener("focus", () => { const r = tile.getBoundingClientRect(); show({ clientX: r.right, clientY: r.bottom }); });
    tile.addEventListener("mouseleave", hideTip);
    tile.addEventListener("blur", hideTip);
  });
  document.querySelectorAll("#roadmap .tile[data-day]").forEach(tile => {
    const d = days.find(x => x.day === +tile.dataset.day);
    const show = ev => showTip(ev, `<b>Day ${dd(d.day)} · ${STATUS[d.status]}</b><div style="margin:3px 0 6px">${md(d.title)}</div>
      <div class="row"><span>estimate</span><span>${d.expected_first ?? "–"} → ${d.expected ?? "–"} PRs</span></div>
      <div class="row"><span>track PRs merged</span><span>${d.track_prs}</span></div>
      <div class="row"><span>criteria ticked</span><span>${d.criteria.ticked}/${d.criteria.total}</span></div>
      <div class="row"><span>tagged new / hold</span><span>${d.criteria.new} / ${d.criteria.hold}</span></div>
      <div class="row"><span>merged on</span><span>${d.worked ? shortDate(d.worked[0]) + (d.worked[1] !== d.worked[0] ? " – " + shortDate(d.worked[1]) : "") : "–"}</span></div>
      ${OOO.has(d.day) ? `<div class="row"><span>runs between</span><span>${ranBetween(d.day).join(" and ")}</span></div>` : ""}`);
    tile.addEventListener("mousemove", show);
    tile.addEventListener("focus", () => { const r = tile.getBoundingClientRect(); show({ clientX: r.right, clientY: r.bottom }); });
    tile.addEventListener("mouseleave", hideTip);
    tile.addEventListener("blur", hideTip);
  });
}

/* ---------- evidence ---------- */
// Counted from each finished day's ticked criteria; the test states are main's, from the script.
function drawEvidence() {
  const days = DATA.days.filter(d => finished(d) && d.evidence.some(c => c.ticked));
  // A day written before the evidence format was never asked to record these: its zero is "not measured".
  const cell = (n, of, pre) => pre ? `<td class="r pre">${n ? `${n}/${of}` : "not measured"}</td>`
    : `<td class="r${n ? "" : " none"}">${n}/${of}</td>`;
  document.getElementById("evidence").innerHTML = `<thead><tr><th>Day</th><th class="r">Cite a PR</th><th class="r">Name a test</th><th class="r">Seen red, recorded</th><th class="r">Tests on main</th><th>Title</th></tr></thead><tbody>` +
    days.map(d => {
      const ev = d.evidence.filter(c => c.ticked), states = ev.flatMap(c => Object.values(c.tests));
      // A retired test is a loss a later day chose, so only skippable and missing tests turn the cell orange.
      const off = states.filter(s => s === "skippable" || s === "missing").length;
      const kept = states.filter(s => s === "present").length, retired = states.filter(s => s === "retired").length;
      const pre = !d.evidence_format;
      return `<tr${pre ? ` title="Spec written before the evidence format"` : ""}><td class="num">${dd(d.day)}${pre ? ` <span class="pre-tag">pre-format</span>` : ""}</td>${cell(ev.filter(c => c.prs.length).length, ev.length, pre)}${cell(ev.filter(c => Object.keys(c.tests).length).length, ev.length, pre)}${cell(ev.filter(c => c.red).length, ev.length, !d.break_format)}<td class="r${off ? " none" : ""}">${states.length ? `${kept}/${states.length} present${retired ? `, ${retired} retired` : ""}` : "–"}</td><td class="t">${md(d.title)}</td></tr>`;
    }).join("") + "</tbody>";
  const gaps = days.flatMap(d => d.evidence.flatMap(c => Object.entries(c.tests).filter(([, s]) => s !== "present")
    .map(([t, s]) => `<li>Day ${dd(d.day)}: <code>${esc(t)}</code> ${s === "retired" ? "is retired: a later day's spec names it and its PR removed it" : `is ${s}`} · ${md(c.claim)}</li>`)));
  document.getElementById("evidence-gaps").innerHTML = `<h3>Named tests not running on main</h3>` +
    (gaps.length ? `<ul>${gaps.join("")}</ul>` : `<p>None: every test a finished day names is on <code>main</code>, and none can be skipped.</p>`);
  // A check first seen red in the closing PR waited a whole day to be shown it can fail.
  const late = days.flatMap(d => (d.broken_at_close || []).map(id => `<li>Day ${dd(d.day)}: ${md(id)}</li>`));
  document.getElementById("evidence-gaps").innerHTML += `<h3>Criteria first broken at the close</h3>` +
    (late.length ? `<ul>${late.join("")}</ul>` : `<p>None: every ticked criterion whose break cites a PR was broken before its day's closing PR.</p>`);
  // A check's state at one merge: found lines, read no files (so its nothing proves nothing), or clean.
  // Since is the merge where the state on main began; problems sort first.
  const REMOVAL = { found: [0, "var(--code)"], empty: [1, "var(--muted)"], clean: [2, "var(--close)"] };
  const removals = (DATA.removals || []).map(c => {
    const state = k => c.hits[k] ? "found" : c.files[k] ? "clean" : "empty", last = c.hits.length - 1, now = state(last);
    let k = last;
    while (k > 0 && state(k - 1) === now) k--;
    return { ...c, now, from: c.since + k, n: c.hits[last], reads: c.files[last] };
  }).sort((a, b) => REMOVAL[a.now][0] - REMOVAL[b.now][0] || a.day - b.day);
  const chip = c => `<span class="chip" style="color:${REMOVAL[c.now][1]}">${c.now === "found" ? `${c.n} line${c.n === 1 ? "" : "s"}` : c.now === "empty" ? "reads no files" : "clean"}</span>`;
  const found = c => c.found.length ? `<tr class="found"><td></td><td colspan="5"><ul>${c.found.map(f =>
    `<li><code>${esc(f.path)}:${f.line}</code> ${esc(f.text)}</li>`).join("")}${c.n > c.found.length ? `<li>and ${c.n - c.found.length} more</li>` : ""}</ul></td></tr>` : "";
  document.getElementById("removals").innerHTML = !removals.length
    ? `<tbody><tr><td>None: no finished day's criteria or Verify name a grep that must print nothing.</td></tr></tbody>` : `<thead><tr><th>Day</th><th>Check</th><th>From</th><th>On main</th><th class="r">Files read</th><th>Since</th></tr></thead><tbody>` +
    removals.map(c => `<tr><td class="num">${dd(c.day)}</td><td class="t"><code>${esc(c.cmd)}</code></td><td class="num">${esc(c.base)}</td><td>${chip(c)}</td><td class="r num">${c.reads}</td>` +
      `<td class="num" title="${esc(R[c.from].title || "")}">${prLabel(R[c.from])}${c.from === c.since ? " · day end" : ""}</td></tr>${found(c)}`).join("") + "</tbody>";
}

// The report scripts/evidence.py wrote, as spec-drift.py read it. No report is "not checked", and a
// day the report leaves out is too: neither is ever drawn as a pass.
const CHECK_KINDS = [["named_test", "Named tests"], ["verify", "Verify"], ["break", "Breaks replayed"]];
function drawEvidenceChecks() {
  const ec = DATA.evidence_checks || { state: "absent" }, box = document.getElementById("evidence-checks");
  if (ec.state === "absent") {
    box.innerHTML = `<p class="off">Not checked: no report. <code>python scripts/evidence.py specs/day-*.md --json docs/dashboard/evidence-checks.json</code> writes one.</p>`;
    return;
  }
  if (ec.state !== "loaded") {
    box.innerHTML = `<p class="load-error">Report unavailable: ${esc(ec.diagnostic || ec.state)}</p>`;
    return;
  }
  // passed / (passed + failed + error): with none run, no share.
  const cell = n => {
    if (!n) return `<td class="r pre">none</td>`;
    const ran = n.passed + n.failed + n.error, na = n.not_applicable ? ` · ${n.not_applicable} n/a` : "";
    return ran ? `<td class="r${n.passed < ran ? " none" : ""}">${n.passed}/${ran} passed${n.error ? `, ${n.error} error` : ""}${na}</td>`
      : `<td class="r pre">–${na}</td>`;
  };
  const r = ec.runner || {};
  box.innerHTML = `<p class="limit">Report of ${esc(String(ec.generated).replace("T", " "))} · ${esc(ec.ref)} at ${esc(String(ec.ref_sha || "none").slice(0, 7))} · runner ${esc(String(r.revision || "unknown").slice(0, 7))}${r.dirty ? " (edited)" : ""}</p>` +
    `<div class="scroll-x"><table><thead><tr><th>Day</th><th>Report</th>${CHECK_KINDS.map(([, h]) => `<th class="r">${h}</th>`).join("")}</tr></thead><tbody>` +
    ec.days.map(d => d.state === "not checked"
      ? `<tr><td class="num">${dd(d.day)}</td><td class="pre" colspan="${CHECK_KINDS.length + 1}">not checked: the report leaves this day out</td></tr>`
      : `<tr${d.state === "stale" ? ` class="off" title="${esc(d.why.join("; "))}"` : ""}><td class="num">${dd(d.day)}</td><td>${d.state === "stale" ? `<span class="pre-tag">stale</span> ${esc(d.why.join("; "))}` : "current"}</td>${CHECK_KINDS.map(([k]) => cell(d.counts[k])).join("")}</tr>`).join("") +
    `</tbody></table></div>`;
  const problems = ec.days.flatMap(d => (d.problems || []).map(p =>
    `<li>Day ${dd(d.day)}: ${esc(p.status)} · ${esc(p.label)}${p.diagnostic ? ` → ${esc(p.diagnostic)}` : ""}</li>`));
  if (problems.length) box.innerHTML += `<h3>Failed or could not run</h3><ul>${problems.join("")}</ul>`;
}

/* ---------- hand-offs ---------- */
// The script sorts them by the receiving day's place in the run order, open ones first; those no
// later day picks up (`to` null) come last, then tracks a PR announced that no branch names.
function drawHandOffs() {
  const groups = [];
  for (const h of DATA.hand_offs || []) {
    if (!groups.length || groups[groups.length - 1].to !== h.to) groups.push({ to: h.to, items: [] });
    groups[groups.length - 1].items.push(h);
  }
  const unopened = DATA.days.flatMap(d => (d.tracks_unopened || []).map(t =>
    ({ source: `Day ${dd(d.day)}`, text: `Track ${t.track}: announced in #${t.pr}, no branch names it`, picked: false })));
  if (unopened.length) groups.push({ to: "tracks", items: unopened });
  const label = g => g.to === null ? "<b>No later day picks these up</b>"
    : g.to === "tracks" ? "<b>Tracks announced, never opened</b>"
    : `<b>Day ${dd(g.to)}</b> ${(d => d ? md(d.title) : "")(DATA.days.find(x => x.day === g.to))}`;
  const source = s => s.startsWith("Day ") ? esc(s) : `<code title="${esc(s)}">${esc(s.split("/").pop())}</code>`;
  document.getElementById("hand-offs").innerHTML = groups.length ? groups.map((g, k) => {
    const open = g.items.filter(h => !h.picked);
    return `<details${k === 0 || g.to === null || g.to === "tracks" ? " open" : ""}><summary>${label(g)} · <span class="${open.length ? "none" : ""}">${open.length} open</span> of ${g.items.length}</summary>
      <ul>${g.items.map(h => `<li class="${h.picked ? "picked" : ""}">${source(h.source)}${h.id ? ` <code>${esc(h.id)}</code>` : ""}: ${md(h.text)}${h.picked ? " <em>(picked up)</em>" : ""}</li>`).join("")}</ul></details>`;
  }).join("") : "<p>No finished day names a day not yet run.</p>";
}

/* ---------- conclusion ---------- */
// Everything here comes from the lab notebook at the head of main or from git, so it cannot go
// stale between refreshes. When the notebook falls behind the phases, the panel says so.
function drawConclusion() {
  const days = DATA.days, now = DATA.now, { reads, measured } = DATA.conclusion;
  const phases = [...new Set(days.map(d => d.phase))].filter(p => p != null);
  const closed = Math.max(-1, ...phases.filter(p => days.filter(d => d.phase === p).every(finished)));
  const read = reads[reads.length - 1];
  document.getElementById("conclusion-eyebrow").textContent = closed < 0
    ? "Conclusion · no phase closed yet"
    : `Conclusion · after Phase ${closed} · ${N - 1} merges`;
  const behind = !read ? `<p class="stale">The lab notebook has no phase read yet.</p>`
    : read.phase < closed ? `<p class="stale">The lab notebook's latest read is from the end of Phase ${read.phase}; Phase ${closed} has closed since and has none yet.</p>` : "";
  document.getElementById("read").innerHTML = behind + (read
    ? read.text.split(/\n\s*\n/).map(p => `<p>${md(p.replace(/\s*\n\s*/g, " "))}</p>`).join("")
    : "");

  const done = days.filter(finished).length;
  const cur = days.find(d => d.day === now.current_day);
  let next = `<p><b>${md(now.next_step)}</b></p>`;
  if (cur) {
    const ds = days.filter(d => d.phase === cur.phase);
    const left = ds.filter(d => !finished(d)).length;
    const prov = days.filter(d => d.status === "provisional");
    next = `<p><b>${md(now.next_step)}</b></p>
      <p>Phase ${cur.phase}, ${PHASES[cur.phase] || ""}, ends at Day ${dd(ds[ds.length - 1].day)}: ${left} day${left === 1 ? "" : "s"} left, counting this one.</p>
      ${prov.length ? `<p>${prov.length} day specs are still provisional, from Day ${dd(prov[0].day)} on.</p>` : ""}`;
  }
  document.getElementById("facts").innerHTML = `
    <div class="finding"><h3>By the count</h3>
      <p>${done} of ${days.length} days finished (${days.filter(d => d.status === "closed").length} with boxes open, ${days.filter(d => d.status === "cut").length} cut), ${closed + 1} of ${phases.length} phases closed. ${N - 1} merges to <code>main</code>:
      ${nKind("code")} code tracks, ${nKind("spec")} spec changes, ${nKind("close")} closing PRs and ${nKind("other")} others.</p></div>
    <div class="finding"><h3>What is next</h3>${next}</div>`;

  document.getElementById("measured").innerHTML = measured.length
    ? `<thead><tr><th>Question</th><th>Evidence so far</th></tr></thead><tbody>` +
      measured.map(([q, e]) => `<tr><td class="t">${md(q)}</td><td class="t">${md(e)}</td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="t">The lab notebook has no measurement table.</td></tr></tbody>`;
}

/* ---------- tokens ---------- */
const MODEL_VAR = { "claude-opus-5-5": "var(--m-opus)", "claude-opus-5": "var(--m-opus-prev)", "claude-sonnet-5": "var(--m-sonnet)", "claude-haiku-4-5": "var(--m-haiku)", "claude-haiku-5-5": "var(--m-haiku)" };
const modelName = m => m.replace(/^claude-/, "").replace(/^(\w)(\w*)-/, (_, a, b) => a.toUpperCase() + b + " ").replace(/-/g, ".");
const kilo = n => n >= 1e6 ? fmt(n / 1e6, 2) + "M" : n >= 1e3 ? fmt(n / 1e3, 0) + "k" : String(n);
const pct = (a, b) => fmt(b ? a / b * 100 : 0, 1) + "%";
function drawTokens() {
  const T = DATA.tokens || [];
  drawHooks(T);
  const box = document.getElementById("token-bars");
  if (!T.length) {
    box.innerHTML = '<p class="load-error">No token counts yet: run <code>python scripts/token-usage.py</code>.</p>';
    document.getElementById("token-agents").innerHTML = '<p class="load-error">No agents to show: there are no token counts to split by agent.</p>';
    return;
  }
  const sum = (rows, k) => rows.reduce((a, r) => a + r[k], 0);
  // Every reply re-reads the whole conversation, so context per reply is what a long session costs.
  const ctx = rows => sum(rows, "input") + sum(rows, "cache_write") + sum(rows, "cache_read");
  const perReply = rows => kilo(Math.round(ctx(rows) / Math.max(1, sum(rows, "replies"))));
  const models = [...new Set(T.map(r => r.model))].sort((a, b) => sum(T.filter(r => r.model === b), "output") - sum(T.filter(r => r.model === a), "output"));
  const color = m => MODEL_VAR[m] || "var(--other)";
  document.getElementById("token-legend").innerHTML = models.map(m =>
    `<span><i class="dot" style="background:${color(m)}"></i>${esc(modelName(m))} · ${kilo(sum(T.filter(r => r.model === m), "output"))}</span>`).join("")
    // A row counted before costs were has none: the total is then a floor, and with none at all it is not shown.
    + (T.some(r => r.cost) ? `<span>$${fmt(T.reduce((a, r) => a + (r.cost || 0), 0), 2)} at list prices, the main sessions $${fmt(T.filter(r => r.agent === "main").reduce((a, r) => a + (r.cost || 0), 0), 2)}</span>` : "");
  const who = r => r.agent || "unknown";
  const impl = T.filter(r => who(r) === "implementer"), main = T.filter(r => who(r) === "main");
  // A row counted before agents were, or a subagent with no type beside it, belongs to no agent: not the checkers'.
  const unknown = T.filter(r => who(r) === "unknown");
  document.getElementById("token-implementer").innerHTML = impl.length
    ? `<b>The implementer</b> (which writes the track code from a brief) used <b>${pct(ctx(impl), ctx(T))}</b> of the context read (${kilo(ctx(impl))} of ${kilo(ctx(T))}) and <b>${pct(sum(impl, "output"), sum(T, "output"))}</b> of the output (${kilo(sum(impl, "output"))} of ${kilo(sum(T, "output"))}); the main sessions (specs, review, the breaks on purpose, the pull requests) <b>${pct(sum(main, "output"), sum(T, "output"))}</b> of the output; ${unknown.length ? `tokens with no agent recorded <b>${pct(sum(unknown, "output"), sum(T, "output"))}</b>; ` : ""}the checking agents the rest.`
    : "";
  drawAgents(T, sum, ctx, perReply, color);
  const dates = [...new Set(T.map(r => r.date))].sort();
  const H = 260, P = { l: 56, r: 12, t: 12, b: 46 }, bw = (LW - P.l - P.r) / dates.length;
  const perDate = dates.map(d => T.filter(r => r.date === d));
  const top = Math.max(...perDate.map(rs => sum(rs, "output")));
  const yMax = Math.ceil(top / 10 ** Math.floor(Math.log10(top)) * 2) / 2 * 10 ** Math.floor(Math.log10(top));
  const ly = v => P.t + (1 - v / yMax) * (H - P.t - P.b);
  box.innerHTML = "";
  const svg = el("svg", { viewBox: `0 0 ${LW} ${H}`, role: "img", "aria-label": "Output tokens per date by model" }, box);
  steps(0, yMax, 4).forEach(t => {
    el("line", { x1: P.l, x2: LW - P.r, y1: ly(t), y2: ly(t), stroke: "var(--grid)" }, svg);
    el("text", { x: P.l - 8, y: ly(t) + 4, "text-anchor": "end", class: "num" }, svg).textContent = kilo(t);
  });
  perDate.forEach((rs, i) => {
    const x = P.l + i * bw;
    let y = 0;
    models.forEach(m => {
      const v = sum(rs.filter(r => r.model === m), "output");
      if (!v) return;
      const rect = el("rect", { x: x + bw * 0.15, y: ly(y + v), width: bw * 0.7, height: ly(y) - ly(y + v), fill: color(m) }, svg);
      rect.addEventListener("mousemove", ev => showTip(ev, `<b>${dates[i]} · ${esc(modelName(m))}</b>
        <div class="row"><span>output</span><span>${kilo(v)}</span></div>
        <div class="row"><span>input + cache</span><span>${kilo(ctx(rs.filter(r => r.model === m)))}</span></div>
        <div class="row"><span>context per reply</span><span>${perReply(rs.filter(r => r.model === m))}</span></div>
        <div class="row"><span>replies</span><span>${sum(rs.filter(r => r.model === m), "replies")}</span></div>`));
      rect.addEventListener("mouseleave", hideTip);
      y += v;
    });
    el("text", { x: x + bw / 2, y: H - P.b + 18, "text-anchor": "middle", class: "num" }, svg).textContent = dates[i].slice(5);
    el("text", { x: x + bw / 2, y: H - P.b + 34, "text-anchor": "middle", class: "num" }, svg).textContent = perReply(rs) + " ctx";
  });
  const days = [...new Set(T.map(r => r.day))].sort((a, b) => rank(a) - rank(b));
  const cell = (rs, m) => { const v = sum(rs.filter(r => r.model === m), "output"); return `<td class="num">${v ? kilo(v) : "–"}</td>`; };
  document.getElementById("token-days").innerHTML =
    `<thead><tr><th>Day</th><th>Replies</th><th>Output</th><th>Input + cache</th><th>Context per reply</th>${models.map(m => `<th>${esc(modelName(m))}</th>`).join("")}</tr></thead><tbody>` +
    days.map(d => {
      const rs = T.filter(r => r.day === d);
      return `<tr><td>${d == null ? "Between days (main, tooling, docs)" : "Day " + dd(d)}</td><td class="num">${sum(rs, "replies")}</td>
        <td class="num">${kilo(sum(rs, "output"))}</td><td class="num">${kilo(ctx(rs))}</td><td class="num">${perReply(rs)}</td>${models.map(m => cell(rs, m)).join("")}</tr>`;
    }).join("") + "</tbody>";
}

// Per date, how the two hooks fared: cut Bash results, and whole Reads of a file over 8,000 bytes.
// Rows counted before the hooks were have no bash field, so they are left out.
function drawHooks(T) {
  const box = document.getElementById("token-hooks");
  const H = T.filter(r => "bash" in r);
  if (!H.length) {
    box.innerHTML = '<caption><p class="load-error">No hook counts yet: run <code>python scripts/token-usage.py</code>.</p></caption>';
    return;
  }
  const sum = (rows, k) => rows.reduce((a, r) => a + r[k], 0);
  const share = (a, b) => `${a} (${b ? pct(a, b) : "–"})`;
  const dates = [...new Set(H.map(r => r.date))].sort();
  box.innerHTML = `<thead><tr><th>Date</th><th>Bash results</th><th>Cut</th><th>Large Reads</th><th>Whole</th></tr></thead><tbody>` +
    dates.map(d => {
      const rs = H.filter(r => r.date === d);
      return `<tr><td>${esc(d)}</td><td class="num">${sum(rs, "bash")}</td><td class="num">${share(sum(rs, "cut"), sum(rs, "bash"))}</td>` +
        `<td class="num">${sum(rs, "reads")}</td><td class="num">${share(sum(rs, "whole"), sum(rs, "reads"))}</td></tr>`;
    }).join("") + "</tbody>";
}

// One bar per agent, longest first, coloured by the model it ran on (the legend above names them).
// HTML, not an SVG: an SVG scales its text with the screen, and at phone width the labels were unreadable.
function drawAgents(T, sum, ctx, perReply, color) {
  const box = document.getElementById("token-agents");
  const label = a => a === "main" ? "main sessions" : a === "unknown" ? "agent not recorded" : a;
  const agents = [...new Set(T.map(r => r.agent || "unknown"))]
    .map(a => ({ a, rs: T.filter(r => (r.agent || "unknown") === a) }))
    .map(o => ({ ...o, out: sum(o.rs, "output"), model: [...new Set(o.rs.map(r => r.model))] }))
    .sort((x, y) => y.out - x.out);
  if (!agents.length) { box.innerHTML = ""; return; }
  const top = agents[0].out, w = v => fmt(Math.max(0.1, v / top * 100), 2) + "%";
  box.innerHTML = `<div class="hbars" role="img" aria-label="Output tokens by agent">` + agents.map((o, i) =>
    `<div class="hbar-row" data-i="${i}"><span class="hbar-label">${esc(label(o.a))}</span><span class="hbar-track">` +
    o.model.map(m => `<i style="width:${w(sum(o.rs.filter(r => r.model === m), "output"))};background:${color(m)}"></i>`).join("") +
    `</span><span class="hbar-value num">${kilo(o.out)}</span></div>`).join("") + "</div>";
  box.querySelectorAll(".hbar-row").forEach(row => {
    const o = agents[+row.dataset.i];
    row.addEventListener("mousemove", ev => showTip(ev, `<b>${esc(label(o.a))}</b>
      <div class="row"><span>model</span><span>${o.model.map(m => esc(modelName(m))).join(", ")}</span></div>
      <div class="row"><span>output</span><span>${kilo(o.out)}</span></div>
      <div class="row"><span>input + cache</span><span>${kilo(ctx(o.rs))}</span></div>
      <div class="row"><span>context per reply</span><span>${perReply(o.rs)}</span></div>
      <div class="row"><span>replies</span><span>${sum(o.rs, "replies")}</span></div>`));
    row.addEventListener("mouseleave", hideTip);
  });
}

// Per finished day, how many tags the blind defect-rater gave the same way; null where the Notes have no counts.
function drawRelabel() {
  const days = DATA.days.filter(finished).sort((a, b) => rank(a.day) - rank(b.day));
  if (!days.length) {
    document.getElementById("relabel").innerHTML = `<tbody><tr><td class="t">No finished day yet: a day is counted once its Notes quote <code>relabel.py compare</code>.</td></tr></tbody>`;
    return;
  }
  const F = ["stage", "cause", "made"];
  const cell = p => !p ? `<td class="r pre">–</td>`
    : `<td class="r"><span class="meter"><i style="width:${p[0] / p[1] * 100}%"></i></span>${p[0]} of ${p[1]}</td>`;
  const tot = F.map(f => days.reduce((a, d) => d.relabel && d.relabel[f] ? [a[0] + d.relabel[f][0], a[1] + d.relabel[f][1]] : a, [0, 0]));
  document.getElementById("relabel").innerHTML = `<thead><tr><th>Day</th>${F.map(f => `<th class="r">${f[0].toUpperCase() + f.slice(1)}</th>`).join("")}<th>Title</th></tr></thead><tbody>` +
    days.map(d => `<tr><td class="num">${dd(d.day)}</td>${d.relabel ? F.map(f => cell(d.relabel[f])).join("")
      : `<td class="r pre" colspan="3">not counted: the Notes give no counts</td>`}<td class="t">${md(d.title)}</td></tr>`).join("") +
    `<tr><td><b>All counted</b></td>${tot.map(p => cell(p[1] ? p : null)).join("")}<td></td></tr></tbody>`;
}

// The infra workflow's last summary on main (scripts/infra-summary.py), and each of its runs on main.
function drawInfra() {
  const I = DATA.infra || { runs: [], latest: null }, L = I.latest, S = L ? L.summary : {};
  const tone = s => ({ pass: "var(--close)", success: "var(--close)", fail: "var(--fail)", failure: "var(--fail)" })[s] || "var(--muted)";
  const chip = (name, state) => `<span class="chip" style="color:${tone(state)}">${esc(name)} · ${esc(state)}</span>`;
  document.getElementById("infra-head").innerHTML = L
    ? `<p>The latest run on main is <a href="${esc(L.url)}"><code>${esc(L.sha)}</code></a> from ${esc(L.date)}: <span class="chip" style="color:${tone(L.conclusion)}">${esc(L.conclusion || "running")}</span></p>`
    : '<p class="load-error">No infra summary on main yet: it appears after the infra workflow next runs on main.</p>';
  document.getElementById("infra-criteria").innerHTML = Object.entries(S.criteria || {}).map(([c, s]) => chip(c, s)).join("");
  document.getElementById("infra-steps").innerHTML = Object.entries(S.steps || {}).map(([n, s]) => chip(n, s)).join("");
  const resources = Object.entries(S.resources || {}).sort((a, b) => b[1] - a[1]);
  document.getElementById("infra-resources").innerHTML = resources.length
    ? `<table><thead><tr><th>Service</th><th>Created</th></tr></thead><tbody>${resources.map(([s, n]) => `<tr><td>${esc(s)}</td><td class="num">${n}</td></tr>`).join("")}</tbody></table>`
    : "";
  const cost = S.cost;
  document.getElementById("infra-cost").innerHTML = cost
    ? `<table><thead><tr><th>Item</th><th>Count</th><th>$ per hour</th></tr></thead><tbody>${cost.items.map(i => `<tr><td>${esc(i.name)}</td><td class="num">${i.count}</td><td class="num">${i.per_hour == null ? "–" : fmt(i.per_hour, 4)}</td></tr>`).join("")}<tr><th>Total</th><td></td><td class="num">$${fmt(cost.per_hour, 3)}</td></tr></tbody></table><p style="font-size:13px;color:var(--muted)">${esc(cost.note)}, ${esc(cost.region)}. A class with no price is listed with no rate and left out of the total.</p>`
    : "";
  document.getElementById("infra-runs").innerHTML = I.runs.slice().reverse().map(r =>
    `<a class="run-sq" href="${esc(r.url)}" style="background:${tone(r.conclusion)}" title="${esc(`${r.sha} ${r.date} ${r.conclusion || "running"}`)}"></a>`).join("");
}

// The live checks of the one deployment (the project's own), each with the time it ran.
function drawDeployment() {
  const D = DATA.deployment, C = D ? D.checks : [];
  const tone = ok => ok ? "var(--close)" : "var(--fail)";
  if (!D) {
    document.getElementById("deployment-head").innerHTML = '<p class="load-error">Nothing deployed yet: the panel fills when a live check has written docs/dashboard/deployment.json.</p>';
    document.getElementById("deployment-checks").innerHTML = "";
    return;
  }
  document.getElementById("deployment-head").innerHTML = `<p>Checked ${esc(D.domain)} at ${esc(D.checkedAt)}: ${C.filter(c => c.ok).length} of ${C.length} checks passed.</p>`;
  document.getElementById("deployment-checks").innerHTML = `<table><thead><tr><th>Criterion</th><th>Check</th><th>Result</th><th>Detail</th><th>Checked at</th></tr></thead><tbody>${C.map(c => `<tr><td>${esc(c.criterion)}</td><td>${esc(c.name)}</td><td><span class="chip" style="color:${tone(c.ok)}">${c.ok ? "pass" : "fail"}</span></td><td>${esc(c.detail)}</td><td>${esc(c.at)}</td></tr>`).join("")}</tbody></table>`;
}

// Display the evidence as supplied by the data build.
const reasonLabels = { replay_failed: "Replay failed", replay_error: "Replay error", replay_stale: "Replay stale",
  evidence_missing: "Evidence missing", not_seen_red: "Not seen red", test_not_found: "Test not found", handoff_unclaimed: "Hand-off unclaimed" };
const attention = DATA.attention || [], criteria = DATA.criteria_index || [];
const unknown = '<span title="not measured">–</span>';
const measured = value => value == null ? unknown : value ? "yes" : "no";
const values = items => items && items.length ? items.map(esc).join(", ") : unknown;
const itemText = item => `<b>${esc(item.id)}</b> · ${esc(reasonLabels[item.reason] || item.reason)}: ${esc(item.detail)}`;
function inspect(id) {
  const row = criteria.find(c => c.id === id), items = attention.filter(a => a.id === id);
  const box = document.getElementById("criterion-inspector");
  if (!id) { box.innerHTML = DATA.criteria_index == null || DATA.attention == null ? "Not in this data" : "Select a criterion or day."; return; }
  const details = row ? `<h3>${esc(row.id)}</h3><p>${esc(row.claim)}</p>
    <p>Spec: ${row.spec ? esc(row.spec) : unknown} · Track: ${row.track == null ? unknown : esc(row.track)}</p>
    <p>PRs: ${values(row.prs)} · Tests: ${values(row.tests)}</p>
    <p>Seen red: ${values(row.seen_red.lines)}</p>
    <p>Replay: ${esc(row.replay.state)} · ${values(row.replay.why)}</p>
    <ul>${row.replay.checks.map(c => `<li>${esc(c.status)}: ${c.diagnostic == null ? unknown : esc(c.diagnostic)}</li>`).join("")}</ul>
    <p>Replaces: ${values(row.replaces)} · Replaced by: ${values(row.replaced_by)}</p>` : `<h3>${esc(id)}</h3>`;
  box.innerHTML = details + `<p>Attention:</p><ul>${items.map(a => `<li>${itemText(a)}</li>`).join("")}</ul>`;
}
function drawCriteria() {
  const list = document.getElementById("attention-list"), matrix = document.getElementById("criteria-matrix");
  list.innerHTML = DATA.attention == null ? "Not in this data" : attention.length ? attention.map((a, i) =>
    `<button type="button" data-attention="${i}">${itemText(a)}</button>`).join("") : "Nothing needs attention";
  matrix.innerHTML = DATA.criteria_index == null ? "<caption>Not in this data</caption>" : !criteria.length ? "<caption>No criteria yet</caption>" :
    `<thead><tr><th>Criterion</th><th>Day</th><th>Tag</th><th>Cites a PR</th><th>Test found</th><th>Seen red</th><th>Replay</th></tr></thead><tbody>${criteria.map((c, i) =>
      `<tr tabindex="0" role="button" data-criterion="${i}"><td>${esc(c.id)}</td><td>${esc(c.day)}</td><td>${esc(c.tag)}</td><td>${measured(c.cited)}</td><td>${measured(c.test_found)}</td><td>${measured(c.seen_red.state)}</td><td>${c.replay.status == null ? unknown : values(c.replay.status)}</td></tr>`).join("")}</tbody>`;
  list.addEventListener("click", e => { const b = e.target.closest("button[data-attention]"); if (b) inspect(attention[+b.dataset.attention].id); });
  const selectRow = e => { const r = e.target.closest("tr[data-criterion]"); if (r) inspect(criteria[+r.dataset.criterion].id); };
  matrix.addEventListener("click", selectRow);
  matrix.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectRow(e); } });
  inspect(attention.length ? attention[0].id : criteria.length ? criteria[0].id : null);
}
panel("attention-list", drawCriteria);

panel("today", drawNow); panel("work-bar", drawWork); panel("perday", drawPerDay); panel("breaks", drawVerification);
panel("infra-head", drawInfra); panel("deployment-head", drawDeployment); panel("token-bars", drawTokens); panel("relabel", drawRelabel);
panel("evidence-gaps", drawEvidence); panel("evidence-checks", drawEvidenceChecks); panel("hand-offs", drawHandOffs); panel("read", drawConclusion);
panel("findings", drawFindings); panel("tbl", drawTable); setSel(sel);
}

function start() {
  if (window.SPEC_DRIFT) return boot(window.SPEC_DRIFT);
  document.getElementById("today").innerHTML = '<p class="load-error">The data did not load. Build the page with <code>python scripts/spec-drift.py --build dashboard.html</code>; if this is the built page, it was published without its data line.</p>';
}
