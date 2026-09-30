/* SOL Memecoin Hunter — iPhone PWA (vanilla JS, no build step).
   Data only comes from our own server (/api/*, access code in a header). No keys live here.
   All text comes from /i18n/vi.json; every server/token string is HTML-escaped before rendering. */
"use strict";

const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch (_) { /* ignore */ } },
};
const S = {
  code: LS.get("accessCode", ""), dict: {}, watch: LS.get("watchlist", []), prefs: LS.get("prefs", {}),
  timer: null, lastStatus: null, busy: false, cache: {}, open: LS.get("openSecs", { overview: true }),
};
const POLL_MS = 15000;
const $ = (sel) => document.querySelector(sel);
const MINT_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

/* ---------------------------------------------------------------- helpers */
function t(key, p) {
  let s = S.dict[key] || key;
  if (p) for (const [k, v] of Object.entries(p)) s = s.split("{" + k + "}").join(v);
  return s;
}
function esc(v) {
  return String(v === null || v === undefined ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function safeUrl(u) { return typeof u === "string" && /^https:\/\//i.test(u) ? u : null; }
function usd(v) {
  if (v === null || v === undefined) return t("common.unknown");
  const a = Math.abs(v);
  if (a >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
  return "$" + v.toFixed(a >= 1 ? 2 : 6);
}
function num(v, d = 0) { return v === null || v === undefined ? t("common.unknown") : Number(v).toLocaleString("vi-VN", { maximumFractionDigits: d }); }
function pctS(v, d = 1) { return v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + v.toFixed(d) + "%"; }
function scoreCls(v) { return v === null || v === undefined ? "c-muted" : v >= 75 ? "c-green" : v >= 55 ? "c-yellow" : v >= 35 ? "c-orange" : "c-muted"; }
function riskCls(v) { return v === null || v === undefined ? "c-muted" : v <= 30 ? "c-green" : v <= 60 ? "c-yellow" : v <= 80 ? "c-orange" : "c-red"; }
function ago(sec) { if (sec === null || sec === undefined) return "—"; return sec < 90 ? sec + "s" : Math.round(sec / 60) + "m"; }
function toast(msg) {
  const el = $("#toast"); el.textContent = msg; el.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove("show"), 1800);
}
function savePrefs() { LS.set("prefs", S.prefs); }

/* ---------------------------------------------------------------- API */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: Object.assign({ "X-Access-Code": S.code }, opts.body ? { "Content-Type": "application/json" } : {}),
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  if (res.status === 401) { showLogin(t("web.login.wrong")); throw new Error("unauthorized"); }
  if (res.status === 503) { showLogin(t("web.login.not_configured")); throw new Error("not_configured"); }
  if (res.status === 429) { const b = await res.json().catch(() => ({})); throw new Error(b.error || "rate_limited"); }
  if (!res.ok) { const b = await res.json().catch(() => ({})); const e = new Error(b.error || "HTTP " + res.status); e.status = res.status; throw e; }
  return res.json();
}

/* ---------------------------------------------------------------- status pills */
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    S.lastStatus = s;
    const sc = $("#pill-scanner"), he = $("#pill-helius");
    if (s.scanner.running) { sc.className = "pill ok"; sc.textContent = t("web.status.running") + " · " + ago(s.scanner.last_cycle_age_s); }
    else if (s.scanner.starting) { sc.className = "pill warn"; sc.textContent = t("web.status.starting"); }
    else { sc.className = "pill bad"; sc.textContent = t("web.status.stopped"); }
    const st = s.helius.state;
    he.className = "pill " + (st === "CONNECTED" ? "ok" : st === "FAILED" ? "bad" : "warn");
    he.textContent = "HELIUS " + st;
  } catch (_) { /* login shown by api() */ }
}

/* ---------------------------------------------------------------- watchlist (localStorage is the source of truth) */
function isWatched(m) { return S.watch.includes(m); }
async function syncWatch() { if (S.watch.length) { try { await api("/api/watch", { method: "POST", body: { mints: S.watch } }); } catch (_) { /* retry next time */ } } }
async function toggleWatch(mint) {
  if (isWatched(mint)) {
    S.watch = S.watch.filter((m) => m !== mint); LS.set("watchlist", S.watch); toast(t("web.watch.removed"));
    try { await api("/api/watch/remove", { method: "POST", body: { mint } }); } catch (_) { /* ignore */ }
  } else {
    if (S.watch.length >= 30) { toast(t("web.watch.full")); return; }
    S.watch.push(mint); LS.set("watchlist", S.watch); toast(t("web.watch.added")); syncWatch();
  }
}

/* ---------------------------------------------------------------- copy CA */
async function copyText(txt) {
  try { await navigator.clipboard.writeText(txt); toast(t("web.copied")); return; } catch (_) { /* fall back */ }
  const ta = document.createElement("textarea"); ta.value = txt; ta.setAttribute("readonly", "");
  ta.className = "offscreen"; document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, txt.length);
  try { document.execCommand("copy"); toast(t("web.copied")); } catch (_) { toast(txt); }
  document.body.removeChild(ta);
}

/* ---------------------------------------------------------------- cards */
function cardHtml(c) {
  const dqCls = c.dq === "VALID" ? "b-valid" : c.dq === "PARTIAL" ? "b-partial" : "b-invalid";
  const early = c.early === null || c.early === undefined ? "—" : c.early;
  return `<a class="card${c.risk > 80 ? " extreme" : ""}" href="#/token/${esc(c.mint)}">
    <div class="card-head">
      <span class="sym">$${esc(c.symbol)}</span><span class="name">${esc(c.name)}</span>
      <button class="star${isWatched(c.mint) ? " on" : ""}" data-star="${esc(c.mint)}" aria-label="${esc(t("web.watch.toggle"))}">${isWatched(c.mint) ? "♥" : "♡"}</button>
    </div>
    <div class="badges">
      <span class="badge ${dqCls}">${esc(c.dq_label)}${c.dq_score !== null ? " " + esc(c.dq_score) : ""}</span>
      <span class="badge">${esc(c.lifecycle_label)}</span>
      <span class="badge">${esc(c.age)}</span>
      ${c.is_early ? `<span class="badge b-early">⚡ EARLY</span>` : ""}
      ${c.early_suppressed ? `<span class="badge b-supp">${esc(t("web.badge.suppressed"))}</span>` : ""}
      ${c.filters_passed ? `<span class="badge c-accent">${esc(t("lbl.pass"))}</span>` : ""}
    </div>
    <div class="scores">
      <div class="score"><div class="k">Opp</div><div class="v ${scoreCls(c.opp)}">${c.opp ?? "—"}</div></div>
      <div class="score"><div class="k">${esc(t("col.risk"))}</div><div class="v ${riskCls(c.risk)}">${c.risk ?? "—"}</div></div>
      <div class="score"><div class="k">Early</div><div class="v ${scoreCls(c.early)}">${early}</div></div>
    </div>
    <div class="stats">
      <div><span class="k">MC</span><span class="v">${esc(c.mc_label)}</span></div>
      <div><span class="k">${esc(t("col.liq"))}</span><span class="v">${esc(c.liq_label)}</span></div>
      <div><span class="k">Vol 5m</span><span class="v">${esc(c.vol5m_label)}</span></div>
      <div><span class="k">${esc(t("col.bs"))}</span><span class="v">${c.bs === null || c.bs === undefined ? esc(t("common.unknown")) : c.bs.toFixed(2)}</span></div>
      <div><span class="k">${esc(t("col.holders"))}</span><span class="v">${c.holders === null || c.holders === undefined ? esc(t("common.unknown")) : num(c.holders)}</span></div>
      <div><span class="k">Top10</span><span class="v">${c.top10 === null || c.top10 === undefined ? esc(t("common.unknown")) : c.top10.toFixed(1) + "%"}</span></div>
    </div>
  </a>`;
}

/* ---------------------------------------------------------------- list views */
const SORTS = {
  opp: (c) => c.opp ?? -1, early: (c) => c.early ?? -1, risk_low: (c) => -(c.risk ?? 101), mc: (c) => c.mc ?? -1,
  vol5m: (c) => c.vol5m ?? -1, age: (c) => -(c.age_min ?? 1e9), holders: (c) => c.holders ?? -1,
};
const LIST_DEFAULT_SORT = { top: "opp", new: "age", early: "early", whales: "holders", dev: "risk_low", social: "opp" };

function listPrefs(kind) {
  S.prefs[kind] = Object.assign({ sort: LIST_DEFAULT_SORT[kind] || "opp", valid: false, lowRisk: false, pass: false, q: "" }, S.prefs[kind] || {});
  return S.prefs[kind];
}
function applyFilters(items, p) {
  const q = (p.q || "").trim().toLowerCase();
  let out = items.filter((c) =>
    (!p.valid || c.dq === "VALID") && (!p.lowRisk || (c.risk !== null && c.risk <= 60)) && (!p.pass || c.filters_passed) &&
    (!q || (c.symbol || "").toLowerCase().includes(q) || (c.name || "").toLowerCase().includes(q) || c.mint.toLowerCase() === q));
  const key = SORTS[p.sort] || SORTS.opp;
  return out.sort((a, b) => key(b) - key(a));
}
function toolbarHtml(kind, p) {
  const opt = (v) => `<option value="${v}"${p.sort === v ? " selected" : ""}>${esc(t("web.sort." + v))}</option>`;
  const chip = (k) => `<button class="chip${p[k] ? " on" : ""}" data-chip="${k}">${esc(t("web.filter." + k))}</button>`;
  return `<div class="toolbar">
    <div class="row">
      <input class="search" id="q" type="search" placeholder="${esc(t("web.search"))}" value="${esc(p.q)}" autocomplete="off" autocapitalize="off" spellcheck="false">
      <select class="select" id="sort">${Object.keys(SORTS).map(opt).join("")}</select>
    </div>
    <div class="chips">${chip("valid")}${chip("lowRisk")}${chip("pass")}</div>
  </div>`;
}
async function renderList(kind, silent) {
  const p = listPrefs(kind);
  const view = $("#view");
  if (!silent) view.innerHTML = `<h1>${esc(t("web.title." + kind))}</h1>${toolbarHtml(kind, p)}<div id="list"><div class="spin">${esc(t("web.loading"))}</div></div>`;
  bindToolbar(kind);
  try {
    const data = await api(`/api/list/${kind}?limit=300`);
    S.cache[kind] = data.items;
    drawList(kind);
  } catch (e) { if (!silent) $("#list").innerHTML = `<div class="empty">${esc(t("web.error"))}: ${esc(e.message)}</div>`; }
}
function drawList(kind) {
  const p = listPrefs(kind), items = applyFilters(S.cache[kind] || [], p), el = $("#list");
  if (!el) return;
  const note = kind === "top" ? `<div class="count">${esc(t("app.top_subtitle"))}</div>` : "";
  el.innerHTML = note + `<div class="count">${esc(t("web.count", { n: items.length }))}</div>` +
    (items.length ? `<div class="cards">${items.map(cardHtml).join("")}</div>` : `<div class="empty">${esc(t("web.empty." + kind))}</div>`);
}
function bindToolbar(kind) {
  const p = listPrefs(kind);
  const q = $("#q"), sort = $("#sort");
  if (q) q.oninput = () => { p.q = q.value; savePrefs(); drawList(kind); };
  if (sort) sort.onchange = () => { p.sort = sort.value; savePrefs(); drawList(kind); };
  document.querySelectorAll("[data-chip]").forEach((b) => {
    b.onclick = () => { const k = b.dataset.chip; p[k] = !p[k]; savePrefs(); b.classList.toggle("on", p[k]); drawList(kind); };
  });
}

/* ---------------------------------------------------------------- watchlist view */
async function renderWatch(silent) {
  const view = $("#view");
  if (!silent) view.innerHTML = `<h1>${esc(t("web.title.watch"))}</h1>
    <div class="toolbar"><div class="row"><input class="search" id="addca" placeholder="${esc(t("lbl.paste_ca"))}" autocomplete="off" autocapitalize="off" spellcheck="false"></div>
    <div class="row"><button class="btn primary" id="addbtn">${esc(t("btn.add"))}</button></div></div><div id="list"></div>`;
  const add = $("#addbtn");
  if (add) add.onclick = async () => {
    const v = $("#addca").value.trim();
    if (!MINT_RE.test(v)) { toast(t("web.watch.bad")); return; }
    if (!isWatched(v)) await toggleWatch(v);
    $("#addca").value = ""; renderWatch(true);
  };
  const el = $("#list");
  if (!S.watch.length) { el.innerHTML = `<div class="empty">${esc(t("web.empty.watch"))}</div>`; return; }
  try {
    const data = await api("/api/watch?mints=" + encodeURIComponent(S.watch.join(",")));
    el.innerHTML = `<div class="cards">${data.items.map((c) => c.pending
      ? `<a class="card" href="#/token/${esc(c.mint)}"><div class="card-head"><span class="sym">${esc(c.mint.slice(0, 6))}…</span><span class="name">${esc(t("web.watch.pending"))}</span>
         <button class="star on" data-star="${esc(c.mint)}">♥</button></div></a>`
      : cardHtml(c)).join("")}</div>`;
  } catch (e) { el.innerHTML = `<div class="empty">${esc(t("web.error"))}: ${esc(e.message)}</div>`; }
}

/* ---------------------------------------------------------------- token page */
function spark(points, keys) {
  const W = 320, H = 90, pad = 4;
  const lines = keys.map(([key, color, fixed]) => {
    const pts = points.filter((p) => p[key] !== null && p[key] !== undefined);
    if (pts.length < 2) return "";
    const xs = pts.map((p) => p.ts), ys = pts.map((p) => p[key]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = fixed ? 0 : Math.min(...ys), y1 = fixed ? 100 : Math.max(...ys);
    const sx = (x) => pad + ((x - x0) / Math.max(1, x1 - x0)) * (W - 2 * pad);
    const sy = (y) => H - pad - ((y - y0) / Math.max(1e-9, y1 - y0)) * (H - 2 * pad);
    return `<polyline fill="none" stroke="${color}" stroke-width="2" points="${pts.map((p) => sx(p.ts).toFixed(1) + "," + sy(p[key]).toFixed(1)).join(" ")}"/>`;
  }).join("");
  return lines ? `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${lines}</svg>` : `<div class="muted small">${esc(t("lbl.no_snapshots"))}</div>`;
}
function kvRow(r) {
  const unknown = r.value.startsWith(t("common.unknown")) || r.value.startsWith(t("common.not_available"));
  const meta = unknown ? "" : `<div class="meta">${esc(r.source)} · ${esc(r.updated)} · ${esc(t("col.data_age"))} ${esc(r.age)} · ${esc(t("col.confidence"))} ${esc(r.confidence)}</div>`;
  return `<div class="kv${unknown ? " unknown" : ""}"><span class="k">${esc(r.label)}</span><span class="v">${esc(r.value)}</span>${meta}</div>`;
}
function sec(key, title, body) {
  return `<details class="sec" data-k="${esc(key)}"${S.open[key] ? " open" : ""}><summary>${esc(title)}</summary><div class="sec-body">${body}</div></details>`;
}

async function renderToken(mint, silent) {
  const view = $("#view");
  if (!silent) view.innerHTML = `<button class="back" id="back">‹ ${esc(t("web.back"))}</button><div class="spin">${esc(t("web.loading"))}</div>`;
  const back = $("#back"); if (back) back.onclick = () => history.length > 1 ? history.back() : (location.hash = "#/top");
  let v;
  try { v = await api("/api/token/" + encodeURIComponent(mint)); }
  catch (e) {
    if (e.status === 404) {
      view.innerHTML = `<button class="back" id="back">‹ ${esc(t("web.back"))}</button>
        <div class="empty">${esc(t("web.token.not_tracked"))}</div><button class="btn primary wide" id="analyze">${esc(t("btn.deep_refresh"))}</button>`;
      $("#back").onclick = () => history.back();
      $("#analyze").onclick = () => doRefresh(mint);
    } else if (!silent) view.querySelector(".spin").textContent = t("web.error") + ": " + e.message;
    return;
  }
  const c = v.card, links = v.links;
  let html = `<button class="back" id="back">‹ ${esc(t("web.back"))}</button>
  <div class="token-head">
    <div class="card-head"><span class="sym">$${esc(c.symbol)}</span><span class="name">${esc(c.name)}</span></div>
    <div class="ca">${esc(c.mint)}</div>
    <div class="scores">
      <div class="score"><div class="k">Opp</div><div class="v ${scoreCls(c.opp)}">${c.opp ?? "—"}</div></div>
      <div class="score"><div class="k">${esc(t("col.risk"))}</div><div class="v ${riskCls(c.risk)}">${c.risk ?? "—"}</div></div>
      <div class="score"><div class="k">${esc(t("col.dq"))}</div><div class="v ${c.dq === "VALID" ? "c-green" : c.dq === "PARTIAL" ? "c-yellow" : "c-red"}">${esc(c.dq_score ?? "—")}</div></div>
    </div>
    <div class="actions">
      <button class="btn primary wide" id="copy">${esc(t("btn.copy_ca"))}</button>
      <a class="btn" href="${esc(links.dexscreener)}" target="_blank" rel="noopener noreferrer">DexScreener</a>
      <a class="btn" href="${esc(links.solscan)}" target="_blank" rel="noopener noreferrer">Solscan</a>
      <a class="btn" href="${esc(links.pumpfun)}" target="_blank" rel="noopener noreferrer">Pump.fun</a>
    </div>
    <div class="actions two">
      <button class="btn" id="watchbtn">${esc(isWatched(c.mint) ? t("web.btn.watching") : t("web.btn.watch"))}</button>
      <button class="btn" id="refresh">${esc(t("web.btn.refresh"))}</button>
    </div>
  </div>`;
  if (v.risk && v.risk.score > 80) html += `<div class="banner red">⚠ ${esc(t("lbl.extreme_risk", { score: v.risk.score }))}</div>`;
  if (c.is_early) html += `<div class="banner green">⚡ EARLY SIGNAL ${esc(c.early)}/100</div>`;
  if (v.data_quality && v.data_quality.status === "INVALID") html += `<div class="banner red">${esc(t("lbl.not_scored_invalid"))}</div>`;

  // overview: scores + why + early + risk flags
  let ov = v.scores.map((s) => `<div class="kv"><span class="k">${esc(s.label)}</span><span class="v">${esc(s.value)}</span>${s.note ? `<div class="meta">${esc(s.note)}</div>` : ""}</div>`).join("");
  if (v.why.length) ov += `<h2>${esc(t("lbl.why"))}</h2>` + v.why.map((w) => `<div class="kv"><span class="k">${esc(w.label)}</span><span class="v c-green">${esc(w.points)}</span><div class="meta">${esc(w.value)} · ${esc(w.source)}</div></div>`).join("");
  if (v.data_quality && v.data_quality.issues.length) ov += `<h2>${esc(t("score.data_quality"))} ${esc(v.data_quality.label)} ${esc(v.data_quality.score)}</h2>` +
    v.data_quality.issues.map((i) => `<div class="flag"><span class="${i.severity === "critical" ? "c-red" : "c-muted"}">${esc(i.text)}</span></div>`).join("");
  html += sec("overview", t("tab.d_overview"), ov);

  if (v.early) {
    const e = v.early;
    let eb = `<div class="kv"><span class="k">${esc(t("score.early_signal"))}</span><span class="v ${scoreCls(e.strength)}">${e.strength === null ? esc(t("common.unknown")) : esc(e.strength) + "/100"} (${esc(e.groups)}/7)</span>${e.strength === null ? `<div class="meta">${esc(e.note)}</div>` : ""}</div>`;
    eb += `<div class="kv"><span class="k">${esc(t("lbl.transition"))}</span><span class="v">${e.transition === null ? esc(t("common.unknown")) : e.transition ? esc(t("common.yes")) : esc(t("common.no"))}</span><div class="meta">${esc(t("lbl.early_rule"))}</div></div>`;
    if (e.suppressed.length) eb += `<div class="banner orange">${esc(t("lbl.suppressed"))}: ${esc(e.suppressed.join("; "))}</div>`;
    eb += e.signals.map((s) => `<div class="sig"><span class="m ${s.state === "fired" ? "c-green" : s.state === "off" ? "" : "c-muted"}">${s.state === "fired" ? "✓" : s.state === "off" ? "·" : "—"}</span>
      <div class="b"><div>${esc(s.label)}</div><div class="meta">${esc(s.value)}${s.blocked ? " [" + esc(s.blocked) + "]" : ""}</div></div></div>`).join("");
    html += sec("early", t("score.early_signal"), eb);
  }
  for (const s of v.sections) {
    let body = s.rows.map(kvRow).join("");
    if (s.key === "holders" && v.holders_top.length) {
      body += `<h2>${esc(t("lbl.top_holders"))}</h2>` + v.holders_top.slice(0, 20).map((h, i) =>
        `<div class="kv"><span class="k">${i + 1}. ${esc(h.owner.slice(0, 4))}…${esc(h.owner.slice(-4))}${h.tags.length ? " · " + esc(h.tags.join(",")) : ""}</span><span class="v">${h.pct.toFixed(2)}%</span></div>`).join("");
    }
    html += sec("m_" + s.key, s.key === "overview" ? t("web.sec.key_metrics") : s.title, body);
  }
  if (v.risk) {
    let rb = `<div class="kv"><span class="k">${esc(t("score.risk"))}</span><span class="v ${riskCls(v.risk.score)}">${esc(v.risk.score)} ${esc(v.risk.level)}</span></div>`;
    rb += v.risk.categories.map((cat) => `<div class="kv"><span class="k">${esc(cat.label)}</span><span class="v ${riskCls(cat.value)}">${esc(cat.value)}</span><meter min="0" max="100" value="${Number(cat.value) || 0}"></meter></div>`).join("");
    rb += v.risk.flags.map((f) => `<div class="flag"><div class="t">⚠ +${esc(f.points)} ${esc(f.label)}</div><div>${esc(f.detail)}</div><div class="muted small">${esc(f.category)} · ${esc(f.source)}</div></div>`).join("") ||
      `<div class="muted">${esc(t("common.none"))}</div>`;
    rb += `<div class="muted small">${esc(t("lbl.not_measurable"))}: ${esc(v.risk.not_measurable.join(", "))}</div>`;
    html += sec("risk", t("tab.d_risk"), rb);
  }
  html += sec("events", t("tab.d_events"), v.events.length ? v.events.map((e) =>
    `<div class="flag"><div class="${e.severity === "critical" ? "c-red" : e.severity === "warning" ? "c-orange" : e.severity === "positive" ? "c-green" : ""}"><b>${esc(e.type)}</b> · ${new Date(e.ts * 1000).toLocaleTimeString("vi-VN")}</div><div>${esc(e.detail)}</div></div>`).join("")
    : `<div class="muted">${esc(t("common.none"))}</div>`);
  html += `<details class="sec" id="histsec" data-k="history"${S.open.history ? " open" : ""}><summary>${esc(t("tab.d_history"))}</summary><div class="sec-body" id="hist"><div class="spin">…</div></div></details>`;
  if (v.filters.length) html += `<div class="muted small">${esc(t("lbl.filters"))}: ${esc(v.filters.join(", "))}</div>`;
  html += `<div class="disclaimer">${esc(v.disclaimer)}</div>`;
  view.innerHTML = html;
  view.querySelectorAll("details.sec").forEach((d) => d.addEventListener("toggle", () => { S.open[d.dataset.k] = d.open; LS.set("openSecs", S.open); }));
  $("#back").onclick = () => history.length > 1 ? history.back() : (location.hash = "#/top");
  $("#copy").onclick = () => copyText(c.mint);
  $("#watchbtn").onclick = async () => { await toggleWatch(c.mint); $("#watchbtn").textContent = isWatched(c.mint) ? t("web.btn.watching") : t("web.btn.watch"); };
  $("#refresh").onclick = () => doRefresh(c.mint);
  const hs = $("#histsec");
  const loadHist = async () => {
    try {
      const h = await api(`/api/token/${encodeURIComponent(mint)}/history`);
      $("#hist").innerHTML = `<div class="small muted">${esc(t("chart.mc"))}</div>${spark(h.points, [["mc", "#14f195"]])}
        <div class="small muted">${esc(t("chart.scores"))}</div>${spark(h.points, [["opp", "#14f195", true], ["risk", "#ef4444", true], ["early", "#9945ff", true]])}
        <div class="legend"><span><i class="dot d-green"></i>Opp</span><span><i class="dot d-red"></i>${esc(t("col.risk"))}</span><span><i class="dot d-purple"></i>Early</span></div>`;
    } catch (_) { $("#hist").textContent = t("web.error"); }
  };
  if (hs.open) loadHist();
  hs.addEventListener("toggle", () => { if (hs.open) loadHist(); });
}
async function doRefresh(mint) {
  toast(t("web.refreshing"));
  try { await api(`/api/token/${encodeURIComponent(mint)}/refresh`, { method: "POST" }); renderToken(mint, true); toast(t("web.refreshed")); }
  catch (e) { toast(e.message === "rate_limited" ? t("web.rate_limited") : t("web.error") + ": " + e.message); }
}

/* ---------------------------------------------------------------- more / narrative / events / status */
function renderMore() {
  const item = (href, key, na) => `<a href="${href}"><span>${esc(t(key))}</span>${na ? `<span class="na">${esc(t("common.not_available"))}</span>` : "<span>›</span>"}</a>`;
  $("#view").innerHTML = `<h1>${esc(t("web.nav.more"))}</h1><div class="menu">
    ${item("#/list/whales", "tab.whales")}${item("#/list/dev", "tab.dev")}${item("#/list/social", "tab.social")}
    ${item("#/narrative", "tab.narrative")}${item("#/events", "lbl.live_events")}${item("#/smart", "tab.smart_money", true)}
    ${item("#/status", "web.title.status")}
    <button id="logout"><span>${esc(t("web.login.change"))}</span><span>›</span></button>
  </div><div class="disclaimer">${esc(t("app.disclaimer"))}</div>`;
  $("#logout").onclick = () => { LS.del("accessCode"); S.code = ""; showLogin(""); };
}
async function renderNarrative() {
  const view = $("#view");
  view.innerHTML = `<h1>${esc(t("tab.narrative"))}</h1><div class="count">${esc(t("lbl.narrative_note"))}</div><div id="list"><div class="spin">${esc(t("web.loading"))}</div></div>`;
  try {
    const d = await api("/api/narratives");
    $("#list").innerHTML = d.items.length ? `<div class="cards">${d.items.map((n) => `<div class="card">
      <div class="card-head"><span class="sym">${esc(n.label)}</span><span class="name">${esc(t("col.tokens"))}: ${esc(n.tokens)}</span></div>
      <div class="stats">
        <div><span class="k">${esc(t("col.new_30m"))}</span><span class="v">${esc(n.new_30m)}</span></div>
        <div><span class="k">${esc(t("col.prev_30m"))}</span><span class="v">${esc(n.prev_30m)}</span></div>
        <div><span class="k">${esc(t("col.vol5m_sum"))}</span><span class="v">${esc(usd(n.vol_5m))}</span></div>
        <div><span class="k">${esc(t("col.narrative_score"))}</span><span class="v c-muted">${esc(t("common.not_available"))}</span></div>
      </div><div class="muted small">${esc((n.top || []).map((s) => "$" + s).join(" · "))}</div></div>`).join("")}</div>`
      : `<div class="empty">${esc(t("common.none"))}</div>`;
  } catch (e) { $("#list").textContent = t("web.error"); }
}
async function renderEvents(silent) {
  const view = $("#view");
  if (!silent) view.innerHTML = `<h1>${esc(t("lbl.live_events"))}</h1><div id="list"><div class="spin">${esc(t("web.loading"))}</div></div>`;
  try {
    const d = await api("/api/events?limit=150");
    $("#list").innerHTML = d.items.length ? d.items.map((e) => `<a class="card" href="#/token/${esc(e.mint)}">
      <div class="card-head"><span class="sym">$${esc(e.symbol)}</span><span class="name">${new Date(e.ts * 1000).toLocaleTimeString("vi-VN")}</span></div>
      <div class="${e.severity === "critical" ? "c-red" : e.severity === "warning" ? "c-orange" : e.severity === "positive" ? "c-green" : ""}"><b>${esc(e.type)}</b></div>
      <div class="muted small">${esc(e.detail)}</div></a>`).join('<div class="spacer"></div>') : `<div class="empty">${esc(t("common.none"))}</div>`;
  } catch (e) { if (!silent) $("#list").textContent = t("web.error"); }
}
async function renderStatus(silent) {
  await refreshStatus();
  const s = S.lastStatus; if (!s) return;
  const kv = (k, v, cls) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v ${cls || ""}">${esc(v)}</span></div>`;
  $("#view").innerHTML = `<h1>${esc(t("web.title.status"))}</h1><div class="token-head">
    ${kv(t("web.status.scanner"), s.scanner.running ? t("web.status.running") : s.scanner.starting ? t("web.status.starting") : t("web.status.stopped"), s.scanner.running ? "c-green" : "c-red")}
    ${kv(t("web.status.last_scan"), ago(s.scanner.last_cycle_age_s))}
    ${kv(t("web.status.cycle"), s.scanner.cycle)}${kv(t("web.status.tracked"), s.scanner.tracked)}
    ${kv(t("web.status.interval"), s.scanner.interval_s + "s")}
    ${kv("HELIUS", s.helius.state + (s.helius.status ? " · HTTP " + s.helius.status : "") + (s.helius.ms ? " · " + s.helius.ms + " ms" : ""), s.helius.state === "CONNECTED" ? "c-green" : "c-red")}
    ${kv("VALID / PARTIAL / INVALID", `${s.data_quality.VALID} / ${s.data_quality.PARTIAL} / ${s.data_quality.INVALID}`)}
    ${kv("EARLY = TRUE", s.early_true)}${kv("SOL", usd(s.sol_usd))}
    ${kv(t("web.status.uptime"), ago(s.scanner.uptime_s))}${kv(t("web.status.version"), s.version)}
  </div><div class="disclaimer">${esc(t("web.status.note"))}</div>`;
}

/* ---------------------------------------------------------------- login */
function showLogin(err) {
  stopPoll();
  $("#tabbar").classList.add("hidden");
  $("#view").innerHTML = `<div class="login"><img src="/apple-touch-icon.png" alt="">
    <h1>SOL Memecoin Hunter</h1><div class="muted">${esc(t("web.login.prompt"))}</div>
    <input id="code" type="password" inputmode="text" autocomplete="current-password" placeholder="${esc(t("web.login.placeholder"))}">
    <div class="err">${esc(err || "")}</div><button class="btn primary" id="go">${esc(t("web.login.submit"))}</button></div>`;
  const go = async () => {
    S.code = $("#code").value.trim();
    try {
      await api("/api/auth"); LS.set("accessCode", S.code); $("#tabbar").classList.remove("hidden");
      refreshStatus(); syncWatch(); if (!location.hash) location.hash = "#/top"; route();
    }
    catch (_) { /* message shown by api() */ }
  };
  $("#go").onclick = go;
  $("#code").onkeydown = (e) => { if (e.key === "Enter") go(); };
}

/* ---------------------------------------------------------------- router + polling */
function stopPoll() { clearInterval(S.timer); S.timer = null; }
function startPoll(fn) {
  stopPoll();
  S.timer = setInterval(() => { if (!document.hidden) { refreshStatus(); fn(); } }, POLL_MS);
}
function setTab(tab) { document.querySelectorAll(".tabbar a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab)); }
function route() {
  if (!S.code) { showLogin(""); return; }
  const h = location.hash || "#/top";
  const parts = h.slice(2).split("/");
  window.scrollTo(0, 0);
  const [p0, p1] = parts;
  if (p0 === "token" && p1) { setTab(""); renderToken(p1); startPoll(() => renderToken(p1, true)); return; }
  if (["top", "new", "early"].includes(p0)) { setTab(p0); renderList(p0); startPoll(() => renderList(p0, true)); return; }
  if (p0 === "list" && ["whales", "dev", "social"].includes(p1)) { setTab("more"); renderList(p1); startPoll(() => renderList(p1, true)); return; }
  if (p0 === "watch") { setTab("watch"); renderWatch(); startPoll(() => renderWatch(true)); return; }
  if (p0 === "narrative") { setTab("more"); renderNarrative(); stopPoll(); return; }
  if (p0 === "events") { setTab("more"); renderEvents(); startPoll(() => renderEvents(true)); return; }
  if (p0 === "status") { setTab("more"); renderStatus(); startPoll(() => renderStatus(true)); return; }
  if (p0 === "smart") { setTab("more"); $("#view").innerHTML = `<h1>${esc(t("tab.smart_money"))}</h1><div class="token-head">${esc(t("na.smart_money"))}</div><div class="disclaimer">${esc(t("na.rule"))}</div>`; stopPoll(); return; }
  setTab("more"); renderMore(); stopPoll();
}

document.addEventListener("click", (e) => {
  const star = e.target.closest("[data-star]");
  if (star) {
    e.preventDefault(); e.stopPropagation();
    toggleWatch(star.dataset.star).then(() => { star.classList.toggle("on", isWatched(star.dataset.star)); star.textContent = isWatched(star.dataset.star) ? "♥" : "♡"; });
  }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.code) { refreshStatus(); } });
window.addEventListener("hashchange", route);

(async function init() {
  try { S.dict = await (await fetch("/i18n/vi.json", { cache: "no-cache" })).json(); } catch (_) { S.dict = {}; }
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  if (!S.code) { showLogin(""); return; }
  refreshStatus(); syncWatch(); route();
})();
