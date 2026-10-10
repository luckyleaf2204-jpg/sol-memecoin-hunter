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
  offset: 0, home: null,
};
/* UI refresh (the server refreshes data on its own tiers: market 5-10s, holders 30-60s, dev 60-120s) */
const POLL = { bot: 3000, home: 5000, token: 5000, list: 10000, watch: 10000, events: 10000, status: 10000 };
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
function nowS() { return Date.now() / 1000 + S.offset; }            // server clock
function sinceTs(ts) { return ts ? Math.max(0, Math.round(nowS() - ts)) : null; }
function syncClock(serverTime) { if (serverTime) S.offset = serverTime - Date.now() / 1000; }
function updSpan(ts) { return `<span class="upd" data-ts="${Number(ts) || ""}">${esc(updText(ts))}</span>`; }
function updText(ts) { const s = sinceTs(ts); return s === null ? t("web.upd.never") : t("web.upd.ago", { s: ago(s) }); }
function tickUpd() { document.querySelectorAll(".upd[data-ts]").forEach((el) => { el.textContent = updText(Number(el.dataset.ts) || null); }); }
function hhmm(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }) : "—"; }
function short(mint) { return mint.slice(0, 4) + "…" + mint.slice(-4); }
function gainTxt(p) {
  if (p.gain_x === null || p.gain_x === undefined) return esc(t("web.nodata"));
  const cls = p.gain_x >= 1 ? "c-green" : "c-red";
  return `<span class="${cls}">${p.gain_pct > 0 ? "+" : ""}${esc(p.gain_pct.toFixed(0))}% (${esc(p.gain_x.toFixed(2))}×)</span>`;
}
function unk(v, f) { return v === null || v === undefined ? `<span class="c-muted">${esc(t("common.unknown"))}</span>` : esc(f ? f(v) : v); }
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
    const noData = s.feeds && s.scanner.uptime_s > 120 && s.feeds.with_market === 0;
    if (s.scanner.running && noData) { sc.className = "pill warn"; sc.textContent = t("web.status.no_data"); }
    else if (s.scanner.running) { sc.className = "pill ok"; sc.textContent = t("web.status.running") + " · " + ago(s.scanner.last_cycle_age_s); }
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
      ${c.identity === "CONFLICT" ? `<span class="badge b-invalid">⚠ ${esc(c.identity_label)}</span>` : c.identity === "UNVERIFIED" ? `<span class="badge">${esc(c.identity_label)}</span>` : ""}
      ${c.pre_early && c.pre_early.status === "PRE_EARLY" ? `<span class="badge b-pre">⚡ PRE-EARLY ${esc(c.pre_early.fired)}/${esc(c.pre_early.total)}</span>` : ""}
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
    ${c.profile ? `<div class="meta">${esc(t("web.pf.initial"))} ${esc(c.profile.initial_label)} → ${esc(c.mc_label)} · ${gainTxt(c.profile)}${c.profile.mc_path.length > 2 ? " · " + esc(c.profile.mc_path.join(" → ")) : ""}</div>` : ""}
    ${c.group_reasons && c.group_reasons.length ? `<div class="meta">${esc(c.group_reasons.join(" · "))}</div>` : ""}
    ${c.pre_early && c.pre_early.status === "PRE_EARLY" ? `<div class="meta c-accent">⚡ ${esc(c.pre_early.reasons.join(" · "))} · ${esc(c.pre_early.data)}</div>` : ""}
    <div class="foot">${updSpan(c.updated_at)}${c.hot && c.hot.length ? " · ⚑ " + esc(c.hot.join(", ")) : ""}</div>
  </a>`;
}

/* ---------------------------------------------------------------- coin profile */
function scenarioHtml(p) {
  if (!p.scenario) return `<span class="c-muted">${esc(t("web.nodata"))}</span>`;
  const lv = p.scenario.levels.map((l) => `${esc(l.label)} <span class="c-muted">(${esc(l.multiple)}×${l.reached ? " · " + esc(t("web.scn.reached")) : ""})</span>`).join(" · ");
  const refs = p.scenario.refs.map((r) => `${esc(r.label)} ${esc(r.value)} <span class="c-muted">(${esc(r.source)})</span>`).join(" · ");
  const b = p.scenario.basis;
  return `<div class="meta">${esc(t("web.scn.levels"))}</div>` + lv +
    `<div class="meta">${esc(t("web.scn.basis", { mc: b.label, src: b.source }))}</div>` +
    (refs ? `<div class="meta">${esc(t("web.scn.refs"))}: ${refs}</div>` : "");
}
function devHtml(d) {
  if (!d.known) return `<span class="c-muted">${esc(t("web.dev.unknown"))}</span>` + (d.wallet ? ` <span class="meta">${esc(short(d.wallet))}</span>` : "");
  const parts = [];
  if (d.prev_tokens !== null) parts.push(esc(t("web.dev.tokens", { n: d.prev_tokens })));
  if (d.graduated !== null) parts.push(esc(t("web.dev.graduated", { n: d.graduated })));
  if (d.best_ath) parts.push(esc(t("web.dev.best", { v: d.best_ath })));
  if (d.dead !== null && d.dead > 0) parts.push(`<span class="c-orange">${esc(t("web.dev.dead", { n: d.dead }))}</span>`);
  if (d.holding_pct !== null) parts.push(esc(t("web.dev.holding", { v: d.holding_pct.toFixed(2) })));
  if (d.sold_pct !== null) parts.push(esc(t("web.dev.sold", { v: d.sold_pct.toFixed(0) })));
  if (d.status) parts.push(esc(d.status));
  return parts.join(" · ") || `<span class="c-muted">${esc(t("web.dev.unknown"))}</span>`;
}
function arrow(v, unit, digits) {
  if (v === null || v === undefined) return "";
  const cls = v > 0 ? "c-green" : v < 0 ? "c-red" : "c-muted";
  return ` <span class="${cls}">${v > 0 ? "↑+" : v < 0 ? "↓" : "→"}${esc(Number(v).toFixed(digits || 0))}${esc(unit || "")}</span>`;
}
function onchainHtml(c) {
  const o = c.profile.onchain;
  const holders = o.holders === null ? `<span class="c-muted">${esc(t("common.unknown"))}</span>` : esc(num(o.holders)) + arrow(o.holders_chg_15m, "/15m");
  const buy = o.buy_share === null ? `<span class="c-muted">${esc(t("common.unknown"))}</span>` : esc(o.buy_share.toFixed(0) + "%") + arrow(o.buy_pp_5m, "pp");
  const cell = (k, v) => `<div><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`;
  return `<div class="stats three">
    ${cell(t("col.holders"), holders)}${cell(t("web.oc.whale"), unk(o.whale))}${cell(t("web.oc.buy"), buy)}
    ${cell("Vol 5m", esc(c.vol5m_label) + arrow(o.vol_chg_5m, "%"))}${cell("Txn 5m", unk(o.txns_5m, num))}${cell(t("col.liq"), esc(c.liq_label))}
    ${cell("Top10", unk(c.top10, (v) => v.toFixed(1) + "%"))}${cell(t("col.risk"), `<span class="${riskCls(c.risk)}">${unk(c.risk)}</span>`)}
    ${cell("Early", c.early === null ? `<span class="c-muted">${esc(t("common.unknown"))} (${esc(c.early_groups)}/7)</span>` : `<span class="${scoreCls(c.early)}">${esc(c.early)}</span> <span class="c-muted">(${esc(t("web.oc.fired", { n: c.fired ?? 0 }))})</span>`)}
  </div><div class="meta">${esc(t("web.oc.pair"))}: ${esc(o.pair_label)}</div>`;
}
function socialHtml(p) {
  const s = p.social, links = [];
  for (const [k, lab] of [["twitter", "X"], ["telegram", "Telegram"], ["website", "Website"]]) {
    const u = safeUrl(s.links[k]); if (u) links.push(`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${lab}</a>`);
  }
  return (links.length ? links.join(" · ") : `<span class="c-muted">${esc(t("web.nodata"))}</span>`) +
    `<div class="meta">${esc(t("web.soc.category"))}: ${s.category.length ? esc(s.category.join(", ")) : esc(t("web.nodata"))} · ${esc(t("web.soc.activity"))}: ${esc(t("web.nodata"))}</div>`;
}
function pathHtml(p) {
  if (!p.mc_path.length) return `<span class="c-muted">${esc(t("web.nodata"))}</span>`;
  const mig = (p.migrations || []).map((m) => esc(t("web.pf.migrated", { time: hhmm(m.ts), before: m.before || "—", after: m.after }))).join(" · ");
  return esc(p.mc_path.join(" → ")) + (mig ? `<div class="meta">${mig}</div>` : "");
}
function profileLine(label, body) { return `<div class="pl"><div class="plk">${esc(label)}</div><div class="plv">${body}</div></div>`; }
function oppCardHtml(c) {
  const p = c.profile, m = encodeURIComponent(c.mint);
  const age = c.age_min === null ? t("common.unknown") : c.age;
  return `<div class="card opp">
    <div class="card-head">
      <span class="sym">🔥 $${esc(c.symbol)}</span><span class="name">${esc(c.name)}</span>
      <button class="star${isWatched(c.mint) ? " on" : ""}" data-star="${esc(c.mint)}" aria-label="${esc(t("web.watch.toggle"))}">${isWatched(c.mint) ? "♥" : "♡"}</button>
    </div>
    <div class="meta">CA ${esc(short(c.mint))} · ${esc(t("web.pf.found", { time: hhmm(p.first_seen) }))} · ${esc(t("web.pf.age"))} ${esc(age)}</div>
    <div class="mcrow">
      <div><span class="k">MC</span><span class="v">${esc(c.mc_label)}</span></div>
      <div><span class="k">${esc(t("web.pf.initial"))}</span><span class="v">${esc(p.initial_label)}</span></div>
      <div><span class="k">${esc(t("web.pf.gain"))}</span><span class="v">${gainTxt(p)}</span></div>
    </div>
    ${profileLine(t("web.pf.scenario"), scenarioHtml(p))}
    ${profileLine("DEV", devHtml(p.dev))}
    ${profileLine("ON-CHAIN", onchainHtml(c))}
    ${profileLine("SOCIAL", socialHtml(p))}
    ${profileLine(t("web.pf.history"), pathHtml(p))}
    ${c.group_reasons.length ? `<div class="meta">${esc(t("web.pf.why_group"))}: ${esc(c.group_reasons.join(" · "))}</div>` : ""}
    <div class="actions four">
      <a class="btn primary" href="#/token/${esc(c.mint)}">${esc(t("web.btn.detail"))}</a>
      <button class="btn" data-copy="${esc(c.mint)}">${esc(t("btn.copy_ca"))}</button>
      <a class="btn" href="https://dexscreener.com/solana/${esc(m)}" target="_blank" rel="noopener noreferrer">DEXSCREENER</a>
      <a class="btn" href="https://solscan.io/token/${esc(m)}" target="_blank" rel="noopener noreferrer">SOLSCAN</a>
    </div>
    <div class="foot">${updSpan(c.updated_at)} · ${esc(t("web.pf.not_advice"))}</div>
  </div>`;
}

/* ---------------------------------------------------------------- list views */
/* key -> value (higher = first). null/undefined = unknown -> always last, never treated as 0 */
const neg = (v) => (v === null || v === undefined ? null : -v);
const SORTS = {
  confirm: (c) => (c.fired === null || c.fired === undefined ? null : c.fired * 1000 + (c.early ?? 0)),
  newest: (c) => c.first_seen ?? null, mc_low: (c) => neg(c.mc), mc_rise: (c) => c.mc_rise, vol_rise: (c) => c.vol_rise,
  buy: (c) => c.buy_pp, holder_rise: (c) => c.holder_rise, risk_low: (c) => neg(c.risk), early: (c) => c.early,
  liq: (c) => c.liq, dev_hist: (c) => c.dev_hist,
  mc_high: (c) => c.mc, momentum: (c) => c.momentum, confidence: (c) => tierConf(c), buy_share: (c) => c.buy_share,
  opp: (c) => c.opp, mc: (c) => c.mc, vol5m: (c) => c.vol5m, age: (c) => neg(c.age_min), holders: (c) => c.holders,
};
const HOME_SORTS = ["confirm", "newest", "mc_low", "mc_rise", "vol_rise", "buy", "holder_rise", "risk_low", "early", "liq", "dev_hist"];
const LIST_SORTS = ["opp", "early", "risk_low", "mc", "vol5m", "age", "holders", "newest", "mc_low", "mc_rise", "vol_rise", "buy", "holder_rise", "liq", "dev_hist"];
const LIST_DEFAULT_SORT = { home: "confirm", top: "opp", new: "age", early: "early", whales: "holders", dev: "risk_low", social: "opp" };
function sortBy(items, sortKey) {
  const key = SORTS[sortKey] || SORTS.opp;
  return items.map((c) => [key(c), c]).sort((a, b) => {
    const x = a[0], y = b[0];
    if (x === null || x === undefined || Number.isNaN(x)) return (y === null || y === undefined || Number.isNaN(y)) ? 0 : 1;
    if (y === null || y === undefined || Number.isNaN(y)) return -1;
    return y - x;
  }).map((p) => p[1]);
}

function listPrefs(kind) {
  S.prefs[kind] = Object.assign({ sort: LIST_DEFAULT_SORT[kind] || "opp", valid: false, lowRisk: false, pass: false, q: "" }, S.prefs[kind] || {});
  return S.prefs[kind];
}
function applyFilters(items, p) {
  const q = (p.q || "").trim().toLowerCase();
  let out = items.filter((c) =>
    (!p.valid || c.dq === "VALID") && (!p.lowRisk || (c.risk !== null && c.risk <= 60)) && (!p.pass || c.filters_passed) &&
    (!q || (c.symbol || "").toLowerCase().includes(q) || (c.name || "").toLowerCase().includes(q) || c.mint.toLowerCase() === q));
  return sortBy(out, p.sort);
}
function toolbarHtml(kind, p) {
  const opt = (v) => `<option value="${v}"${p.sort === v ? " selected" : ""}>${esc(t("web.sort." + v))}</option>`;
  const sorts = kind === "home" ? HOME_SORTS : LIST_SORTS;
  const chip = (k) => `<button class="chip${p[k] ? " on" : ""}" data-chip="${k}">${esc(t("web.filter." + k))}</button>`;
  return `<div class="toolbar">
    <div class="row">
      <input class="search" id="q" type="search" placeholder="${esc(t("web.search"))}" value="${esc(p.q)}" autocomplete="off" autocapitalize="off" spellcheck="false">
      <select class="select" id="sort">${sorts.map(opt).join("")}</select>
    </div>
    <div class="chips">${chip("valid")}${chip("lowRisk")}${chip("pass")}</div>
  </div>`;
}
async function renderList(kind, silent) {
  const p = listPrefs(kind);
  const view = $("#view");
  if (!silent) view.innerHTML = `<h1>${esc(t("web.title." + kind))}</h1>${kind === "new" ? '<section id="bwkw-shadow"></section>' : ""}${toolbarHtml(kind, p)}<div id="list"><div class="spin">${esc(t("web.loading"))}</div></div>`;
  bindToolbar(kind);
  try {
    const data = await api(`/api/list/${kind}?limit=300`);
    syncClock(data.server_time);
    S.cache[kind] = data.items;
    drawList(kind);
    if (kind === "new") await renderBwkwShadow();
  } catch (e) { if (!silent) $("#list").innerHTML = `<div class="empty">${esc(t("web.error"))}: ${esc(e.message)}</div>`; }
}
async function renderBwkwShadow() {
  const el = $("#bwkw-shadow"); if (!el) return;
  try {
    const d = await api("/api/shadow/bwkw");
    const events = d.events || [], positions = d.positions || [];
    const fmtSol = (v) => v === null || v === undefined ? "—" : Number(v).toFixed(4) + " SOL";
    el.innerHTML = `<section class="token-head shadow-card">
      <div class="shadow-title"><strong>🎯 Paper copy · BwWK17cb</strong><span class="badge">${esc(d.mode)}</span></div>
      <div class="muted small"><a href="https://solscan.io/account/${encodeURIComponent(d.target)}" target="_blank" rel="noopener">${esc(d.target.slice(0,8))}…${esc(d.target.slice(-6))} ↗</a> · Theo dõi từ lúc khởi động</div>
      <div class="banner orange">Chỉ mô phỏng, không gửi lệnh thật. Khớp theo báo giá Jupiter khi phát hiện giao dịch; kết quả không tương đương tốc độ sniper.</div>
      ${d.last_error ? `<div class="banner red">Theo dõi tạm lỗi: ${esc(d.last_error)}</div>` : ""}
      ${d.coverage && d.coverage !== "Watching new signatures from startup; earlier activity excluded" ? `<div class="banner">${esc(d.coverage)}</div>` : ""}
      <div class="shadow-kpis"><div><span>Đã chốt</span><b class="${d.realized_sol >= 0 ? "c-green" : "c-red"}">${fmtSol(d.realized_sol)}</b></div><div><span>Vị thế mở</span><b>${positions.length}</b></div><div><span>Trạng thái</span><b>${d.running ? "Đang theo dõi" : "Đang khởi động"}</b></div></div>
      ${positions.length ? `<h3>Vị thế paper đang mở</h3><div class="shadow-rows">${positions.map(x => `<div><a href="https://solscan.io/token/${encodeURIComponent(x.mint)}" target="_blank" rel="noopener">${esc(x.mint.slice(0,6))}…${esc(x.mint.slice(-5))} ↗</a><span>${(Number(x.token_raw) / (10 ** Number(x.decimals))).toPrecision(5)} token</span><span>Giá vốn ${fmtSol(Number(x.cost_lamports)/1e9)}</span></div>`).join("")}</div>` : ""}
      <h3>Giao dịch mô phỏng gần đây</h3>${events.length ? `<div class="shadow-rows">${events.map(x => `<div><span>${esc(x.side)} · ${new Date(Number(x.ts)*1000).toLocaleTimeString("vi-VN")}</span><a href="https://solscan.io/token/${encodeURIComponent(x.mint)}" target="_blank" rel="noopener">${esc((x.mint||"").slice(0,6))}…${esc((x.mint||"").slice(-5))}</a><span class="${x.pnl_sol === null ? "c-muted" : x.pnl_sol >= 0 ? "c-green" : "c-red"}">${x.pnl_sol === null ? esc(x.status) : fmtSol(x.pnl_sol)}</span></div>`).join("")}</div>` : '<div class="empty shadow-empty">Đang chờ giao dịch mới của ví này…</div>'}
    </section>`;
  } catch (e) { el.innerHTML = `<section class="token-head shadow-card"><strong>🎯 Paper copy · BwWK17cb</strong><div class="muted small">Mô-đun chưa sẵn sàng: ${esc(e.message)}</div></section>`; }
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
  const draw = () => (kind === "home" ? drawHome() : drawList(kind));
  if (q) q.oninput = () => { p.q = q.value; savePrefs(); draw(); };
  if (sort) sort.onchange = () => { p.sort = sort.value; savePrefs(); draw(); };
  document.querySelectorAll("[data-chip]").forEach((b) => {
    b.onclick = () => { const k = b.dataset.chip; p[k] = !p[k]; savePrefs(); b.classList.toggle("on", p[k]); draw(); };
  });
}

/* ---------------------------------------------------------------- home: 4 groups */
const GROUPS = [["opportunity", "🔥"], ["watch", "👀"], ["nodata", "⏳"], ["excluded", "⛔"]];
async function renderHome(silent) {
  const p = listPrefs("home");
  if (!silent) {
    $("#view").innerHTML = `<h1>${esc(t("web.title.home"))}</h1><div class="banner">${esc(t("web.home.note"))}</div>${toolbarHtml("home", p)}<div id="list"><div class="spin">${esc(t("web.loading"))}</div></div>`;
    bindToolbar("home");
  }
  try {
    const d = await api("/api/home");
    syncClock(d.server_time);
    S.home = d;
    drawHome();
  } catch (e) { if (!silent && $("#list")) $("#list").innerHTML = `<div class="empty">${esc(t("web.error"))}: ${esc(e.message)}</div>`; }
}
function drawHome() {
  const d = S.home, el = $("#list");
  if (!d || !el) return;
  const p = listPrefs("home");
  if (S.open["g_opportunity"] === undefined) Object.assign(S.open, { g_opportunity: true, g_watch: true, g_nodata: false, g_excluded: false });
  const pre = applyFilters(d.pre_early || [], p);
  const preHtml = `<details class="sec grp g-pre" data-k="g_pre"${S.open.g_pre !== false ? " open" : ""}>
      <summary>⚡ PRE-EARLY <span class="cnt">${esc((d.counts || {}).pre_early || 0)}</span></summary>
      <div class="sec-body"><div class="meta">${esc(t("web.pre.rule"))}</div>
      ${pre.length ? `<div class="cards">${pre.map(cardHtml).join("")}</div>` : `<div class="empty">${esc(t("web.pre.empty"))}</div>`}</div></details>`;
  el.innerHTML = preHtml + GROUPS.map(([g, ico]) => {
    const items = applyFilters(d.groups[g] || [], p);
    const shown = (d.groups[g] || []).length, total = d.counts[g] || 0;
    const body = items.length
      ? `<div class="cards">${items.map(g === "opportunity" ? oppCardHtml : cardHtml).join("")}</div>` +
        (total > shown ? `<div class="count">${esc(t("web.home.more", { n: total - shown }))}</div>` : "")
      : `<div class="empty">${esc(t("web.group_empty." + g))}</div>`;
    return `<details class="sec grp g-${g}" data-k="g_${g}"${S.open["g_" + g] ? " open" : ""}>
      <summary>${ico} ${esc(t("web.group." + g))} <span class="cnt">${esc(total)}</span></summary>
      <div class="sec-body"><div class="meta">${esc(t("web.group_rule." + g))}</div>${body}</div></details>`;
  }).join("") + `<div class="count">${esc(t("web.home.quiet", { n: d.counts.quiet || 0 }))}</div><div class="disclaimer">${esc(t("app.disclaimer"))}</div>`;
  el.querySelectorAll("details.grp").forEach((x) => x.addEventListener("toggle", () => { S.open[x.dataset.k] = x.open; LS.set("openSecs", S.open); }));
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
  const back = $("#back"); if (back) back.onclick = () => history.length > 1 ? history.back() : (location.hash = "#/home");
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
  syncClock(v.server_time);
  const pf = c.profile;
  const claims = (c.identity_claims || []).map((x) => `${esc(x.source)}: ${esc(x.symbol)}${x.name ? " (" + esc(x.name) + ")" : ""}`).join(" · ");
  html += `<div class="banner ${c.identity === "CONFLICT" ? "red" : c.identity === "VERIFIED" ? "green" : ""}">${esc(t("web.id.title"))}: ${esc(c.identity_label)}
    <div class="meta">${claims || esc(t("web.nodata"))}${c.identity === "CONFLICT" ? " — " + esc(t("web.id.conflict_note")) : ""}</div></div>`;
  html += `<div class="token-head">
    <div class="meta">${esc(t("web.pf.found", { time: hhmm(pf.first_seen) }))}${pf.age_at_discovery_min !== null ? " · " + esc(t("web.pf.found_age", { m: pf.age_at_discovery_min })) : ""} · ${esc(t("web.pf.age"))} ${esc(c.age)}</div>
    <div class="mcrow">
      <div><span class="k">MC</span><span class="v">${esc(c.mc_label)}</span></div>
      <div><span class="k">${esc(t("web.pf.initial"))}</span><span class="v">${esc(pf.initial_label)}</span></div>
      <div><span class="k">${esc(t("web.pf.gain"))}</span><span class="v">${gainTxt(pf)}</span></div>
    </div>
    ${pf.initial_ts ? `<div class="meta">${esc(t("web.pf.initial_note", { time: hhmm(pf.initial_ts), src: pf.initial_source }))}</div>` : ""}
    ${profileLine(t("web.pf.scenario"), scenarioHtml(pf))}
    <div class="meta">${esc(t("web.scn.disclaimer"))}</div>
    ${profileLine("DEV", devHtml(pf.dev) + (pf.dev.wallet ? `<div class="meta">${esc(t("web.dev.wallet"))}: ${esc(pf.dev.wallet)}${pf.dev.funding ? " · " + esc(t("web.dev.funding")) + ": " + esc(short(pf.dev.funding)) : ""} · ${esc(t("web.dev.related"))}: ${esc(t("common.not_available"))}</div>` : ""))}
    ${profileLine("ON-CHAIN", onchainHtml(c))}
    ${profileLine("SOCIAL", socialHtml(pf))}
    ${profileLine(t("web.pf.history"), pathHtml(pf))}
    <div class="foot">${esc(t("web.upd.market"))} ${updSpan(pf.updated.market)} · ${esc(t("web.upd.holders"))} ${updSpan(pf.updated.holders)} · DEV ${updSpan(pf.updated.dev)}</div>
  </div>`;
  if (c.group) html += `<div class="banner ${c.group === "excluded" ? "red" : c.group === "opportunity" ? "green" : ""}">${esc(t("web.group." + c.group))}${c.group_reasons.length ? ": " + esc(c.group_reasons.join(" · ")) : ""}</div>`;
  if (v.risk && v.risk.score > 80) html += `<div class="banner red">⚠ ${esc(t("lbl.extreme_risk", { score: v.risk.score }))}</div>`;
  if (c.is_early) html += `<div class="banner green">⚡ EARLY SIGNAL ${esc(c.early)}/100</div>`;
  if (v.data_quality && v.data_quality.status === "INVALID") html += `<div class="banner red">${esc(t("lbl.not_scored_invalid"))}</div>`;

  // overview: scores + why + early + risk flags
  let ov = v.scores.map((s) => `<div class="kv"><span class="k">${esc(s.label)}</span><span class="v">${esc(s.value)}</span>${s.note ? `<div class="meta">${esc(s.note)}</div>` : ""}</div>`).join("");
  if (v.why.length) ov += `<h2>${esc(t("lbl.why"))}</h2>` + v.why.map((w) => `<div class="kv"><span class="k">${esc(w.label)}</span><span class="v c-green">${esc(w.points)}</span><div class="meta">${esc(w.value)} · ${esc(w.source)}</div></div>`).join("");
  if (v.data_quality && v.data_quality.issues.length) ov += `<h2>${esc(t("score.data_quality"))} ${esc(v.data_quality.label)} ${esc(v.data_quality.score)}</h2>` +
    v.data_quality.issues.map((i) => `<div class="flag"><span class="${i.severity === "critical" ? "c-red" : "c-muted"}">${esc(i.text)}</span></div>`).join("");
  html += sec("overview", t("tab.d_overview"), ov);

  if (v.pre_early) {
    const pe = v.pre_early;
    let pb = `<div class="kv"><span class="k">⚡ PRE-EARLY</span><span class="v ${pe.status === "PRE_EARLY" ? "c-green" : pe.status === "BLOCKED" ? "c-red" : "c-muted"}">${esc(pe.label)} · ${esc(pe.fired)}/${esc(pe.total)}</span><div class="meta">${esc(pe.data)} · ${esc(t("web.pre.age", { m: pe.age_min }))}</div></div>`;
    if (pe.blocked_by.length) pb += `<div class="banner red">${esc(t("web.pre.blocked"))}: ${esc(pe.blocked_by.join(" · "))}</div>`;
    pb += pe.signals.map((s) => `<div class="sig"><span class="m ${s.state === "fired" ? "c-green" : s.state === "off" ? "" : "c-muted"}">${s.state === "fired" ? "✓" : s.state === "off" ? "·" : "—"}</span>
      <div class="b"><div>${esc(s.label)}</div><div class="meta">${esc(s.value)} · ${esc(s.rule)}</div></div></div>`).join("");
    pb += `<div class="meta">${esc(t("web.pre.rule"))}</div>`;
    html += sec("pre_early", "⚡ PRE-EARLY", pb);
  }
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
  $("#back").onclick = () => history.length > 1 ? history.back() : (location.hash = "#/home");
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
    ${item("#/top", "web.title.top")}${item("#/list/whales", "tab.whales")}${item("#/list/dev", "tab.dev")}${item("#/list/social", "tab.social")}
    ${item("#/narrative", "tab.narrative")}${item("#/events", "lbl.live_events")}${item("#/smart", "tab.smart_money", true)}
    ${item("#/insiders", "web.title.insiders")}${item("#/status", "web.title.status")}
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
  </div>${feedsTable(s.feeds)}${refreshTable(s.refresh)}<div class="disclaimer">${esc(t("web.status.note"))}</div>`;
}

function feedsTable(f) {
  if (!f || !f.pumpportal) return "";
  const row = (k, v, ok) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v ${ok === true ? "c-green" : ok === false ? "c-red" : "c-muted"}">${esc(v)}</span></div>`;
  const http = (x) => (x.requests ? "HTTP " + (x.last_status ?? "—") + " · " + x.errors + "/" + x.requests + " " + t("web.feed.errors") : t("web.feed.no_calls")) +
    (x.cooldown_s ? " · cooldown " + x.cooldown_s + "s" : "") + (x.last_error && !x.ok ? " · " + x.last_error.slice(0, 80) : "");
  const pp = f.pumpportal;
  return `<h2>${esc(t("web.feed.title"))}</h2><div class="token-head">
    ${row("PumpPortal WS", (pp.connected ? t("web.feed.connected") : t("web.feed.disconnected")) + " · " + t("web.feed.events", { n: pp.events_last_min }) + (pp.last_error && !pp.connected ? " · " + pp.last_error.slice(0, 80) : ""), pp.connected)}
    ${row("Pump.fun", http(f.pumpfun), f.pumpfun.ok)}
    ${row("DexScreener", http(f.dexscreener), f.dexscreener.ok)}
    ${row(t("web.feed.with_market"), f.with_market + " / " + f.tracked, f.with_market > 0)}
    ${f.helius && f.helius.credits ? row(t("web.feed.helius_credits"), `${f.helius.credits.used.toLocaleString("en-US")} / ${f.helius.credits.daily_budget.toLocaleString("en-US")} · ${t("web.feed.remaining")} ${f.helius.credits.remaining.toLocaleString("en-US")}${f.helius.credits.quota_exhausted ? " · QUOTA" : ""}`, !f.helius.credits.quota_exhausted && f.helius.credits.remaining > 0) : ""}
    ${f.helius && f.helius.credits ? row(t("web.feed.helius_rate"), `${f.helius.credits.per_hour.toLocaleString("en-US")}/h · ${f.helius.credits.per_day.toLocaleString("en-US")}/${t("web.feed.day")} · ${t("web.feed.projected")} ${f.helius.credits.projected_month.toLocaleString("en-US")} / ${f.helius.credits.monthly_plan.toLocaleString("en-US")}`, f.helius.credits.projected_month <= f.helius.credits.monthly_plan) : ""}
    ${f.helius ? row(t("web.feed.deep_pool"), `${f.helius.deep_pool} · ×${f.helius.deep_scale}${f.helius.deep_skipped.length ? " · " + t("web.feed.skipped") + ": " + f.helius.deep_skipped.slice(0, 6).join(", ") : ""}`, !f.helius.deep_skipped.length) : ""}
  </div>`;
}
function refreshTable(r) {
  if (!r || !r.tiers) return "";
  const row = (k, target, tier) => `<div class="kv"><span class="k">${esc(t("web.rf." + k))}</span><span class="v">${esc(target)}${tier && tier.measured_s !== null ? " · " + esc(t("web.rf.measured", { s: tier.measured_s })) : ""}${tier && tier.failures ? " · ⚠ " + esc(tier.failures) : ""}</span></div>`;
  const tr = r.tiers;
  return `<h2>${esc(t("web.rf.title"))}</h2><div class="token-head">
    ${row("discovery", "~" + tr.discovery.target_s + "s", tr.discovery)}
    ${row("market", `${r.market_s.hot}s / ${r.market_s.normal}s / ${r.market_s.quiet}s`, tr.market)}
    ${row("holders", `${r.holders_s.hot}s / ${r.holders_s.normal}s`, null)}
    ${row("dev", `${r.dev_s.hot}s / ${r.dev_s.normal}s`, null)}
    ${row("persist", tr.persist.target_s + "s", tr.persist)}
    <div class="kv"><span class="k">${esc(t("web.rf.hot"))}</span><span class="v">${esc(r.hot_tokens)}</span></div>
    <div class="meta">${esc(t("web.rf.note", { n: r.deep_max_per_min }))}</div></div>`;
}



/* ---------------------------------------------------------------- 4 early tiers */
const TIERS = ["pre_early", "watch", "signal", "trade"];
const TIER_SORTS = ["tier", "mc_high", "mc_low", "age", "momentum", "opp", "confidence", "risk_low", "vol_rise", "buy_share"];
function tierConf(c) {
  if (c.trade && c.trade.confidence !== undefined) return c.trade.confidence;
  if (c.early_watch) return c.early_watch.confidence;
  if (c.pre_early) return Math.round(100 * c.pre_early.computable / c.pre_early.total);
  return null;
}
function isComplete(c) {
  if (c.early_watch) return c.early_watch.missing.length === 0;
  if (c.pre_early) return c.pre_early.computable === c.pre_early.total;
  return c.dq === "VALID";
}
function tierLine(tier, c) {
  if (tier === "pre_early" && c.pre_early) {
    const p = c.pre_early;
    return `<div class="meta ${p.status === "PRE_EARLY" ? "c-accent" : ""}">⚡ ${esc(p.label)} · ${esc(p.fired)}/${esc(p.total)} · ${esc(p.data)}${p.blocked_by.length ? " · ⛔ " + esc(p.blocked_by.join(", ")) : ""}${p.reasons.length ? " · " + esc(p.reasons.join(" · ")) : ""}</div>`;
  }
  if (tier === "watch" && c.early_watch) {
    const w = c.early_watch;
    return `<div class="meta">👀 ${esc(t("web.ew.rank"))} ${esc(w.rank)} · Confidence ${esc(w.confidence)}% · ${esc(w.data)}${w.missing.length ? " · " + esc(t("web.ew.missing")) + ": " + esc(w.missing.join(", ")) : ""}</div>`;
  }
  if (tier === "trade" && c.trade) {
    const x = c.trade;
    return `<div class="meta c-green">🟢 Opportunity ${esc(x.opportunity)} · Confidence ${esc(x.confidence)}%${x.size_usd ? " · " + esc(t("web.trade.size")) + " $" + esc(x.size_usd) : ""}${x.holding ? " · " + esc(t("web.trade.holding")) : ""}</div>
      <div class="meta">Why: ${esc(x.why.join(" · "))}</div>`;
  }
  return "";
}
async function renderTiers(tier, silent) {
  tier = TIERS.includes(tier) ? tier : (S.prefs.tier || "pre_early");
  S.prefs.tier = tier; savePrefs();
  const key = "tier_" + tier;
  S.prefs[key] = Object.assign({ sort: "tier", valid: false, lowRisk: false, complete: false, q: "" }, S.prefs[key] || {});
  const p = S.prefs[key];
  if (!silent) {
    const seg = TIERS.map((x) => `<a class="chip${x === tier ? " on" : ""}" href="#/early/${x}">${esc(t("web.tier." + x))}</a>`).join("");
    const opt = (v) => `<option value="${v}"${p.sort === v ? " selected" : ""}>${esc(t("web.sort." + v))}</option>`;
    const chip = (k, lab) => `<button class="chip${p[k] ? " on" : ""}" data-tchip="${k}">${esc(t(lab))}</button>`;
    $("#view").innerHTML = `<div class="chips tiers">${seg}</div>
      <div class="banner">${esc(t("web.tier.rule." + tier))}</div>
      <div class="toolbar"><div class="row">
        <input class="search" id="q" type="search" placeholder="${esc(t("web.search"))}" value="${esc(p.q)}" autocomplete="off" autocapitalize="off" spellcheck="false">
        <select class="select" id="sort">${TIER_SORTS.map(opt).join("")}</select></div>
        <div class="chips">${chip("valid", "web.filter.valid")}${chip("lowRisk", "web.filter.lowRisk")}${chip("complete", "web.filter.complete")}</div></div>
      <div id="list"><div class="spin">${esc(t("web.loading"))}</div></div>`;
    $("#q").oninput = () => { p.q = $("#q").value; savePrefs(); drawTier(tier); };
    $("#sort").onchange = () => { p.sort = $("#sort").value; savePrefs(); drawTier(tier); };
    document.querySelectorAll("[data-tchip]").forEach((b) => { b.onclick = () => { const k = b.dataset.tchip; p[k] = !p[k]; savePrefs(); b.classList.toggle("on", p[k]); drawTier(tier); }; });
  }
  try {
    const d = await api("/api/early/" + tier);
    syncClock(d.server_time);
    d.items.forEach((c, i) => { c._order = i; });
    S.cache[key] = d.items;
    drawTier(tier);
  } catch (e) { if (!silent && $("#list")) $("#list").innerHTML = `<div class="empty">${esc(t("web.error"))}: ${esc(e.message)}</div>`; }
}
function drawTier(tier) {
  const p = S.prefs["tier_" + tier], el = $("#list");
  if (!el) return;
  const q = (p.q || "").trim().toLowerCase();
  let items = (S.cache["tier_" + tier] || []).filter((c) =>
    (!p.valid || c.dq === "VALID") && (!p.lowRisk || (c.risk !== null && c.risk <= 60)) && (!p.complete || isComplete(c)) &&
    (!q || (c.symbol || "").toLowerCase().includes(q) || (c.name || "").toLowerCase().includes(q) || c.mint.toLowerCase() === q));
  items = p.sort === "tier" ? items.sort((a, b) => a._order - b._order) : sortBy(items, p.sort);
  el.innerHTML = `<div class="count">${esc(t("web.count", { n: items.length }))}</div>` + (items.length
    ? `<div class="cards">${items.map((c) => cardHtml(c).replace(/<\/a>$/, tierLine(tier, c) + "</a>")).join("")}</div>`
    : `<div class="empty">${esc(t("web.tier.empty." + tier))}</div>`);
}

/* ---------------------------------------------------------------- paper trading bot */
const MOD_NAMES = { scan: "SCAN", vet: "VET", size: "SIZE", risk: "RISK", fills: "FILLS", book: "BOOK" };
function money(v, sign) {
  if (v === null || v === undefined) return t("common.unknown");
  const a = Math.abs(v), s = v < 0 ? "-" : sign && v > 0 ? "+" : "";
  return s + "$" + a.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function pnlCls(v) { return v === null || v === undefined ? "c-muted" : v > 0 ? "c-green" : v < 0 ? "c-red" : ""; }
function areaChart(points) {
  const W = 320, H = 110, pad = 4;
  if (!points || points.length < 2) return `<div class="muted small">${esc(t("web.bot.no_history"))}</div>`;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = (x) => pad + ((x - x0) / Math.max(1, x1 - x0)) * (W - 2 * pad);
  const sy = (y) => H - pad - ((y - y0) / Math.max(1e-9, y1 - y0)) * (H - 2 * pad);
  const line = points.map((p) => sx(p[0]).toFixed(1) + "," + sy(p[1]).toFixed(1)).join(" ");
  const up = ys[ys.length - 1] >= ys[0], col = up ? "#14f195" : "#ef4444";
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <polygon fill="${col}" fill-opacity="0.15" points="${sx(xs[0]).toFixed(1)},${H} ${line} ${sx(xs[xs.length - 1]).toFixed(1)},${H}"/>
    <polyline fill="none" stroke="${col}" stroke-width="2" points="${line}"/></svg>`;
}
function heartbeat(live) {
  const pts = []; for (let i = 0; i <= 12; i++) { const x = i * 27; pts.push(`${x},18`, `${x + 8},18`, `${x + 11},4`, `${x + 14},32`, `${x + 17},18`); }
  return `<svg class="heart" viewBox="0 0 330 36" preserveAspectRatio="none"><polyline fill="none" stroke="${live ? "#a3e635" : "#ef4444"}" stroke-width="1.5" points="${pts.join(" ")}"/></svg>`;
}
function posHtml(p) {
  const kv = (k, v, cls) => `<div><span class="k">${esc(k)}</span><span class="v ${cls || ""}">${v}</span></div>`;
  const px = (v) => v === null || v === undefined ? esc(t("common.unknown")) : "$" + esc(Number(v).toPrecision(4));
  return `<div class="pos">
    <div class="card-head"><a class="sym" href="#/token/${esc(p.mint)}">$${esc(p.symbol)}</a><span class="name">${esc(short(p.mint))}</span>
      <span class="badge ${p.status === "STALE" ? "b-partial" : "b-valid"}">${esc(p.status)}</span></div>
    <div class="stats">
      ${kv(t("web.bot.entry"), px(p.entry))}${kv(t("web.bot.current"), px(p.current))}
      ${kv(t("web.bot.size"), esc(money(p.size_usd)))}${kv("P&L", `${esc(money(p.pnl_usd, true))} (${p.pnl_pct === null ? "—" : (p.pnl_pct > 0 ? "+" : "") + esc(p.pnl_pct.toFixed(1)) + "%"})`, pnlCls(p.pnl_usd))}
      ${kv("Stop", px(p.stop))}${kv("TP1 / TP2", px(p.tp1) + (p.tp1_done ? " ✓" : "") + " / " + px(p.tp2))}
      ${kv("Trailing", p.trailing_armed ? esc(p.trailing_pct) + "% · " + px(p.trail_price) : esc(t("web.bot.trail_off")))}
      ${kv("Momentum", p.momentum === null || p.momentum === undefined ? esc(t("common.unknown")) : esc(p.momentum))}
      ${kv(t("col.risk"), p.risk === null || p.risk === undefined ? esc(t("common.unknown")) : esc(p.risk), riskCls(p.risk))}
    </div></div>`;
}
/* ================================================================ TRADING BOT DASHBOARD (7 areas) */
const BOT_PAGE = 20;
const CAND_SORTS = {
  score: (x) => (x.opportunity ?? -1) + (x.confidence ?? -1) - (x.risk ?? 100),     // default: Opp + Conf high, Risk low
  opportunity: (x) => x.opportunity, confidence: (x) => x.confidence, momentum: (x) => x.momentum,
  risk_low: (x) => (x.risk === null || x.risk === undefined ? null : -x.risk), mc: (x) => x.mc,
  age: (x) => (x.age_min === null || x.age_min === undefined ? null : -x.age_min),
};
const EXIT_KEYS = ["take_profit", "stop_loss", "liquidity", "momentum", "risk"];
function doingText(d) {
  const x = d.doing || {};
  if (x.kind === "kill") return t("web.bot.do.kill");
  if (x.kind === "holding") return t("web.bot.do.holding", { sym: x.symbol, pnl: x.pnl_pct === null ? "—" : (x.pnl_pct > 0 ? "+" : "") + x.pnl_pct + "%", n: x.count });
  if (x.kind === "candidates") return t("web.bot.do.candidates", { n: x.count });
  if (x.kind === "searching") return t("web.bot.do.searching", { n: x.count });
  return t("web.bot.do.starting");
}
function doingSub(d) {
  const x = d.doing || {};
  if (x.kind === "searching") return t("web.bot.do.evaluating", { n: d.evaluated.length }) + " · " + t("web.bot.do.no_setup");
  if (x.kind === "candidates") return t("web.bot.do.evaluating", { n: d.evaluated.length });
  return "";
}
function stageOf(a) {
  if (a.kind === "DISCOVER") {
    if (/early_signal/.test(a.text)) return ["🎯", "EARLY SIGNAL"];
    if (/early_watch/.test(a.text)) return ["👀", "EARLY WATCH"];
    if (/pre_early/.test(a.text)) return ["⚡", "PRE-EARLY"];
    return ["🔍", "DISCOVER"];
  }
  return { WATCH: ["🟡", "VET"], REJECT: ["❌", "VET"], BLOCK: ["⛔", "RISK"], BUY: ["🟢", "BUY"], SELL: ["🔴", "SELL"],
           FAILED: ["⚠️", "FAILED"], KILL: ["⛔", "KILL"], INFO: ["ℹ️", "INFO"] }[a.kind] || ["•", a.kind];
}
function decisionCls(a) { return a === "BUY" ? "d-buy" : a === "WATCH" || a === "PENDING" ? "d-watch" : "d-reject"; }
function nn(v) { return v === null || v === undefined ? "—" : esc(v); }
const LC_BADGE = { NEW: "🟡 NEW", PRE_MIGRATION: "🟠 PRE-MIGRATION", POST_MIGRATION: "🟢 POST-MIGRATION", UNKNOWN: "⚪ UNKNOWN" };

function lcLine(x) {
  if (!x.lifecycle) return "";
  const s = x.setup_score === null || x.setup_score === undefined ? "—" : Math.round(x.setup_score);
  const p = x.migration_progress === null || x.migration_progress === undefined ? "" : ` · curve ${Number(x.migration_progress).toFixed(0)}%`;
  const ps = x.post_state ? ` · ${x.post_state}` : "";
  const conf = x.setup_confidence === null || x.setup_confidence === undefined ? "—" : Number(x.setup_confidence).toFixed(2);
  return `<div class="c-muted"><b>${esc(LC_BADGE[x.lifecycle] || x.lifecycle)}</b> (${esc(x.lifecycle_confidence || "—")})${esc(p)}${esc(ps)} · ${esc(x.setup_type || "—")} setup ${esc(s)}/${esc(x.setup_threshold ?? "—")} · data ${esc(conf)} · Opp ${esc(x.opportunity ?? "—")} (log)</div>`;
}

function lcSummary(d) {
  const L = d.lifecycle_summary;
  if (!L) return "";
  const row = (k) => { const o = L.by_lifecycle[k] || {}; return `<div class="kv"><span class="k">${esc(LC_BADGE[k])}</span><span class="v">${esc(o.tokens ?? 0)} tokens · ${esc(o.candidates ?? 0)} candidates · ${esc(o.buys ?? 0)} BUY · ${esc(money(o.pnl ?? 0, true))}</span></div>`; };
  const unk = Object.entries(L.unknown_reasons || {}).slice(0, 3).map(([k, v]) => k + " " + v).join(" · ");
  return `<div class="panel"><h3><span>LIFECYCLE</span><span class="meta">${esc(d.engine || "")}</span></h3>${row("NEW")}${row("PRE_MIGRATION")}${row("POST_MIGRATION")}${row("UNKNOWN")}${unk ? `<div class="meta">UNKNOWN: ${esc(unk)}</div>` : ""}</div>`;
}

function sampleReport(d) {
  const R = d.sample_report;
  if (!R) return "";
  const pc = (v) => (v === null || v === undefined ? "—" : esc((v >= 0 ? "+" : "") + Number(v).toFixed(2) + "%"));
  const ci = (c) => (c ? `[${pc(c[0])} … ${pc(c[1])}]` : "—");
  const row = (k, m) => `<tr><td>${esc(k)}</td><td>${pc(m.expectancy_pct)}</td><td>${ci(m.ci95_pct)}</td><td>${esc(m.win_rate_pct ?? "—")}%</td><td>${esc(m.rr_realised ?? "—")}</td><td>${esc(money(m.max_drawdown.usd))}</td></tr>`;
  const t = (tbl) => Object.entries(tbl || {}).map(([k, m]) => row(k, m)).join("");
  const ep = R.epoch || {};
  const warn = (R.warnings || []).map((w) => `<div class="meta c-orange">⚠ ${esc(w)}</div>`).join("");
  return `<div class="panel"><h3><span>SAMPLE REPORT · net expectancy after cost</span><span class="meta">n=${esc(R.n)} · epoch ${esc(ep.fingerprint || "—")} since ${esc(ep.started_at_utc || "—")}</span></h3>${warn}
    <div class="tblw"><table class="tbl"><thead><tr><th>COST</th><th>EXPECTANCY</th><th>95% CI</th><th>WIN</th><th>R:R</th><th>MAX DD</th></tr></thead><tbody>${t(R.by_cost)}</tbody></table></div>
    ${R.haircut && R.haircut.n_haircut_trades ? `<div class="meta c-orange">HAIRCUT exits (no Jupiter SELL quote): ${esc(R.haircut.n_haircut_trades)} trades (${esc(R.haircut.share_pct)}%) · @7% with ${pc(R.haircut.haircut_trades["7%"]?.expectancy_pct)} · without them ${pc(R.haircut.without_haircut_trades["7%"]?.expectancy_pct)}</div>` : ""}
    ${R.gaps && R.gaps.count ? `<div class="meta c-orange">GAPS (bot stopped / feed down > 5 min): ${esc(R.gaps.count)} · ${esc(R.gaps.minutes)} min · ${esc(R.gaps.excluded_trades)} trades excluded</div>` : ""}
    <div class="meta">${esc(R.sample_status || "")} · thresholds 30 (preliminary) / 200 per arm (A/B) · durable store: ${esc((R.durability || {}).store || "—")}</div>
    ${R.costs && R.costs.n ? `<div class="meta">FIXED FEES (network + priority): median ${esc(R.costs.fixed_fee_pct_median)}% per trade (${esc(R.costs.n_tx_median)} tx, size ${esc(money(R.costs.size_usd_median))}) · ${esc(R.costs.share_over_target_pct)}% of trades > 2% · ${esc(R.costs.recommendation || "")}</div>` : ""}
    <div class="meta">STOP GAP −20%: ${Object.entries((R.sl_gap_scenario || {}).by_cost || {}).map(([k, m]) => `${esc(k)} ${pc(m.expectancy_pct)}`).join(" · ")} · TP/(TP+SL) ${esc(R.reference_only?.tp_share ?? "—")} (reference only: volatility) · excluded LEGACY ${esc(R.excluded_legacy_or_noquote)}</div></div>`;
}

function truthPanel(d) {
  const T = d.price_truth;
  if (!T) return "";
  const px = (v) => (v === null || v === undefined ? "—" : esc(Number(v).toPrecision(4)));
  const pc = (v) => (v === null || v === undefined ? "—" : esc((v >= 0 ? "+" : "") + Number(v).toFixed(1) + "%"));
  const open = (T.open || []).map((p) => `<tr><td>${esc(p.symbol || "")}</td><td>${px(p.entry)}</td><td>${p.ds_mark_post_entry ? px(p.ds_mark) : "—"}</td><td>${p.truth === "VALID" ? px(p.jup_sell) : "TRUTH UNKNOWN"}</td><td>${pc(p.old_pnl_pct)}</td><td>${p.truth === "VALID" ? pc(p.truth_pnl_pct) : "TRUTH UNKNOWN"}</td></tr>`).join("");
  const rec = (T.recent || []).map((r) => `<tr><td>${esc(r.symbol || "")}</td><td>${px(r.ds)}</td><td>${px(r.jup_sell)}</td><td>${px(r.entry)}</td><td>${pc(r.discrepancy_pct)}</td><td>${r.ds_age_s == null ? "—" : esc(r.ds_age_s + "s")}</td><td>${esc(r.status)} · ${esc(r.class)}</td></tr>`).join("");
  const kpi = (k, v, cls, sub) => `<div class="kpi"><div class="k">${esc(k)}</div><div class="v ${cls || ""}">${v}</div>${sub ? `<div class="s">${esc(sub)}</div>` : ""}</div>`;
  const S = T.status || {}, X = T.truth || {}, G = T.legacy || {};
  const pp = (o) => (o && o.mean !== null && o.mean !== undefined ? `${pc(o.mean)} / ${pc(o.median)}` : "—");
  const ck = Object.entries(S.checks || {}).map(([k, v]) => `${v ? "✅" : "⏳"} ${esc(k)}`).join(" · ");
  const ec = X.execution_cost || {};
  const status = S.status ? `<div class="panel"><h3><span>TRUTH PRICE STATUS</span><span class="meta ${S.status === "VALIDATED" ? "c-green" : "c-orange"}">${esc(S.status)}</span></h3>
    <div class="kgrid">
      ${kpi("Common-source", esc(S.common_source_n + " / 30"), "", esc((S.trades_total ?? 0) + " closed trades"))}
      ${kpi("VALID quotes", esc(S.quotes_valid ?? 0), "", esc((S.valid_quote_pct ?? "—") + "% · INVALID " + (S.quotes_invalid ?? 0)))}
      ${kpi("contextSlot", esc((S.context_slot_pct ?? "—") + "%"), "", "of VALID quotes")}
      ${kpi("TRUTH P&L", esc(money(X.truth_pnl_usd_total ?? 0, true)), pnlCls(X.truth_pnl_usd_total ?? 0), "executable SELL quotes")}
      ${kpi("Truth win rate", esc(X.win_rate_pct ?? "—") + (X.win_rate_pct == null ? "" : "%"), "", esc("PF " + (X.profit_factor ?? "—")))}
      ${kpi("Truth mean / median", pp(X.truth_pnl_pct), "", "per trade")}
      ${kpi("MFE truth", pp(X.mfe_truth), "", "mean / median")}
      ${kpi("MAE truth", pp(X.mae_truth), "", "mean / median")}
      ${kpi("Execution cost", pp(ec.total_execution_cost_pct), "", "impact + latency (measured) + fees")}
    </div>
    <div class="meta">${ck}</div>
    <div class="meta"><b>LEGACY / REFERENCE (not truth):</b> net ${esc(money(G.net_pnl ?? 0, true))} · ${esc(G.closed ?? 0)} trades · win ${esc(G.win_rate ?? "—")}% · same trades mean ${pc(G.same_trades_mean_pct)} · sign differs from truth ${esc(G.sign_differs_from_truth ?? 0)}</div>
    <div class="meta">Legacy fast-SL: ${esc(S.legacy_fast_sl || "")}</div></div>` : "";
  if (!open && !rec) return status;
  return status + `<div class="panel"><h3><span>PRICE TRUTH</span><span class="meta">${esc(T.label)} · ${esc(T.quotes_last_min)} SELL quotes/min</span></h3>${open ? `<div class="tblw"><table class="tbl"><thead><tr><th>OPEN</th><th>ENTRY</th><th>DS MARK</th><th>JUP SELL</th><th>OLD P&L</th><th>TRUTH P&L</th></tr></thead><tbody>${open}</tbody></table></div>` : ""}${rec ? `<div class="tblw"><table class="tbl"><thead><tr><th>TOKEN</th><th>DS</th><th>JUP SELL</th><th>ENTRY</th><th>DISCREPANCY</th><th>AGE</th><th>STATUS</th></tr></thead><tbody>${rec}</tbody></table></div>` : ""}</div>`;
}

function shadowLine(x) {
  const s = x.shadow;
  if (!s) return "";
  const v = (k) => (s[k] === null || s[k] === undefined ? "—" : esc(s[k]));
  const any = ["money_flow_score", "exit_liquidity_risk", "entry_location", "pre_shadow_decision"].some((k) => s[k] !== null && s[k] !== undefined);
  if (!any) return "";
  return `<div class="c-muted"><b>[SHADOW — không phải lệnh BUY]</b> Money Flow ${v("money_flow_score")} · Independent ${v("independent_buyer_score")} (${v("independence")}) · Cluster ${v("cluster_risk")} · Exit Liquidity ${v("exit_liquidity_risk")} · Entry ${v("entry_location")}${s.pre_shadow_decision ? " · PRE " + v("pre_shadow_decision") : ""}</div>`;
}

function shadowTables(d) {
  const rows = (d.evaluated || []).filter((x) => x.shadow && (x.shadow.money_flow_score != null || x.shadow.exit_liquidity_risk != null || x.shadow.entry_location));
  if (!rows.length) return "";
  const tr = rows.slice(0, 15).map((x) => `<tr><td>${esc(x.symbol || "")}</td><td>${esc(x.lifecycle || "")}</td><td>${esc(x.shadow.money_flow_score ?? "—")}</td><td>${esc(x.shadow.independent_buyer_score ?? "—")}</td><td>${esc(x.shadow.cluster_risk ?? "—")}</td><td>${esc(x.shadow.exit_liquidity_risk ?? "—")}</td><td>${esc(x.shadow.entry_location || "—")}</td><td>${esc(x.action)}</td></tr>`).join("");
  return `<div class="panel"><h3><span>MONEY FLOW · EXIT LIQUIDITY · ENTRY LOCATION</span><span class="meta">SHADOW (research only)</span></h3><div class="tblw"><table class="tbl"><thead><tr><th>TOKEN</th><th>LIFECYCLE</th><th>MONEY FLOW</th><th>INDEP.</th><th>CLUSTER</th><th>EXIT LIQ</th><th>ENTRY</th><th>LIVE ACTION</th></tr></thead><tbody>${tr}</tbody></table></div></div>`;
}

function esLine(x) {
  const e = x.early_score;
  if (!e) return "";
  const f = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(2));
  const ok = e.score !== null && e.score >= e.theta && e.confidence >= e.gamma;
  return `<div class="c-muted">${ok ? "✅" : "⏳"} EarlyScore ${esc(f(e.score))}/${esc(e.theta)} · conf ${esc(f(e.confidence))}/${esc(e.gamma)} · ${esc(e.bucket)} · risk ${esc(e.observed_risk ?? "—")} (prior ${esc(e.prior_risk)})${x.engine === "experimental" ? ` · OLD: ${esc(x.old_decision || "—")}${x.old_candidate ? " (candidate)" : ""}` : ""}</div>`;
}

function waitLine(x) {
  if (x.action === "PENDING") {
    const w = (x.waiting || []).map((k) => t("web.wait." + k));
    return `<div class="c-wait">⏳ ${esc(t("web.wait.pending_id"))}${w.length ? " — " + esc(t("web.wait.for")) + ": " + esc(w.join(" + ")) : ""}</div>`;
  }
  if (x.action === "WATCH") {
    const w = (x.waiting || []).map((k) => t("web.wait." + k));
    return `<div class="c-wait">🟡 ${esc(t("web.wait.title"))}${w.length ? " — " + esc(t("web.wait.for")) + ": " + esc(w.join(" + ")) : ""}</div>`;
  }
  if (x.action === "REJECT" && (x.rejected || []).length) {
    return `<div class="c-wait c-red">🔴 ${esc(x.rejected.map((k) => t("web.rej." + k)).join(" · "))}</div>`;
  }
  return "";
}
function candRow(x) {
  const why = x.why.filter((w) => !/^(waiting|reject): /.test(w)).slice(0, 4).join(" · ");
  return `<div class="crow ${decisionCls(x.action)}">
    <div class="c-tok"><a class="sym" href="#/token/${esc(x.mint)}">$${esc(x.symbol)}</a><span class="ca" data-copy="${esc(x.mint)}" title="${esc(x.mint)}">${esc(short(x.mint))}</span></div>
    <div class="c-n" data-l="MC">${x.mc === null ? "—" : esc(usd(x.mc))}</div>
    <div class="c-n" data-l="Age">${x.age_min === null ? "—" : esc(x.age_min) + "m"}</div>
    <div class="c-n" data-l="Opp"><b>${nn(x.opportunity)}</b></div>
    <div class="c-n" data-l="Mom">${nn(x.momentum)}</div>
    <div class="c-n ${riskCls(x.risk)}" data-l="Risk">${nn(x.risk)}</div>
    <div class="c-n" data-l="Conf">${x.confidence === null || x.confidence === undefined ? "—" : esc(x.confidence) + "%"}</div>
    <div class="c-n ${x.identity === "VERIFIED" ? "c-green" : x.identity === "CONFLICT" ? "c-red" : "c-muted"}" data-l="ID">${x.identity === "VERIFIED" ? "✓ VERIFIED" : esc(x.identity)}</div>
    <div class="c-n ${x.vet === "PASS" ? "c-green" : "c-orange"}" data-l="VET">${esc(x.vet)}</div>
    <div class="c-dec"><span class="dec ${decisionCls(x.action)}">${esc(x.action === "WATCH" ? t("web.wait.badge") : x.action)}</span></div>
    <div class="c-why">${waitLine(x)}${x.action !== "BUY" && (x.blocked_by || []).length ? `<div class="c-muted">BLOCKED_BY: ${esc(x.blocked_by.slice(0, 5).join(", "))}</div>` : ""}${lcLine(x)}${shadowLine(x)}${esLine(x)}<div>${esc(why)}</div></div></div>`;
}
async function renderBot(silent) {
  let d;
  try { d = await api("/api/bot"); } catch (e) { if (!silent) $("#view").innerHTML = `<div class="empty">${esc(t("web.error"))}: ${esc(e.message)}</div>`; return; }
  syncClock(d.server_time);
  const s = d.stats, sc = d.scan, L = d.limits;
  const P = S.prefs;
  P.botFilter = P.botFilter || "ALL";
  P.botSort = CAND_SORTS[P.botSort] ? P.botSort : "score";
  const all = d.evaluated;
  const list = sortBy(all.filter((x) => P.botFilter === "ALL" || x.action === P.botFilter), P.botSort);
  const pages = Math.max(1, Math.ceil(list.length / BOT_PAGE));
  P.botPage = Math.min(P.botPage || 0, pages - 1);
  const page = list.slice(P.botPage * BOT_PAGE, (P.botPage + 1) * BOT_PAGE);
  const locked = (m) => m === "AUTO" && !d.live_available;
  const modeBtn = (m) => `<button class="mode${d.mode === m ? " on" : ""}" data-mode="${m}"${locked(m) ? " disabled" : ""}>${m === "AUTO" ? "AUTO " + (d.mode === "AUTO" ? "ON" : "OFF") : m}${locked(m) ? " 🔒" : ""}</button>`;
  const kpi = (k, v, cls, sub) => `<div class="kpi"><div class="k">${esc(k)}</div><div class="v ${cls || ""}">${v}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
  const cnt = (k, v, hl) => `<div class="cnt${hl ? " hl" : ""}"><b>${esc(v)}</b><span>${esc(t(k))}</span></div>`;
  const chip = (k) => `<button class="chip${P.botFilter === k ? " on" : ""}" data-evf="${k}">${esc(k === "ALL" ? t("web.bot.all") : k)} ${esc(k === "ALL" ? all.length : all.filter((x) => x.action === k).length)}</button>`;
  const sortOpt = (k) => `<option value="${k}"${P.botSort === k ? " selected" : ""}>${esc(t("web.bot.sort." + k))}</option>`;
  const hc = d.helius && d.helius.credits;
  const riskState = d.kill_switch ? "KILL" : d.risk_state;
  const exitCell = (st) => `<span class="ex ex-${esc(st.state)}" title="${esc(st.detail)}">${st.state === "hit" ? "●" : st.state === "near" ? "◐" : st.state === "unknown" ? "?" : "○"}</span>`;
  const posRow = (p) => `<div class="prow"><a class="sym" href="#/token/${esc(p.mint)}">$${esc(p.symbol)}</a>
      <span data-l="Entry">${esc(Number(p.entry).toPrecision(3))}</span>
      <span data-l="Now">${p.current === null ? "—" : esc(Number(p.current).toPrecision(3))}</span>
      <span data-l="Size">${esc(money(p.size_usd))}</span>
      <span data-l="P&amp;L %" class="${pnlCls(p.pnl_pct)}">${p.pnl_pct === null ? "—" : (p.pnl_pct > 0 ? "+" : "") + esc(p.pnl_pct.toFixed(1)) + "%"}</span>
      <span data-l="P&amp;L $" class="${pnlCls(p.pnl_usd)}">${esc(money(p.pnl_usd, true))}</span>
      <span data-l="${esc(t("web.bot.held"))}">${esc(p.holding_min)}m</span>
      <span data-l="Exit" class="${/^EXIT/.test(p.exit_state) ? "c-red" : /^NEAR/.test(p.exit_state) ? "c-orange" : "c-green"}">${esc(p.exit_state)}${p.stale ? " · STALE" : ""}</span></div>`;
  const holds = d.positions.map((p) => ({ ts: d.server_time, kind: "HOLD", symbol: p.symbol, usd: p.pnl_usd,
    text: `${p.pnl_pct === null ? "—" : (p.pnl_pct > 0 ? "+" : "") + p.pnl_pct.toFixed(1) + "%"} · ${p.holding_min}m · ${p.exit_state}` }));
  const timeline = holds.concat(d.activity).slice(0, 60).map((a) => {
    const [ico, stage] = a.kind === "HOLD" ? ["💼", "HOLD"] : stageOf(a);
    return `<div class="tl"><span class="ico">${ico}</span><div><div><b>${esc(stage)}</b> ${a.symbol ? "$" + esc(a.symbol) : ""}${a.usd !== null && a.usd !== undefined ? " · " + esc(money(a.usd, a.kind === "HOLD")) : ""}
      <span class="c-muted">${esc(new Date(a.ts * 1000).toLocaleTimeString("vi-VN"))}</span></div><div class="meta">${esc(a.text)}</div></div></div>`; }).join("");
  const pend = (d.pending || []).map((o) => `<div class="ev"><div class="card-head"><a class="sym" href="#/token/${esc(o.mint)}">$${esc(o.symbol)}</a>
      <span class="name">${esc(money(o.usd))} · ${esc(o.expires_in)}s</span>
      <button class="btn primary" data-approve="${esc(o.id)}">${esc(t("web.bot.approve"))}</button><button class="btn" data-dismiss="${esc(o.id)}">✕</button></div>
      <div class="meta">WHY: ${esc(o.why.join(" · "))}</div></div>`).join("");

  const v = $("#view");
  v.classList.add("bot-wide");
  v.innerHTML = `<div class="botgrid">
  <section class="b-doing doing ${esc(d.doing.kind)}" id="bot-doing">
    <div class="k">🤖 ${esc(t("web.bot.doing"))} <span class="tag">${esc(d.mode)}</span> <span class="live${d.live ? "" : " off"}"><i></i>${d.live ? "RUNNING" : "STOPPED"}</span></div>
    <div class="v">${esc(doingText(d))}</div><div class="meta">${esc(doingSub(d))} · ${updSpan(d.last_tick)} · ${esc(d.version || "")}</div>
    ${d.lifecycle_summary ? `<div class="meta">${esc(d.lifecycle_summary.doing.join(" · "))}</div>` : ""}
  </section>

  <section class="b-status panel" id="bot-status"><h3><span>🤖 BOT STATUS</span><span class="modes">${modeBtn("PAPER")}${modeBtn("CONFIRM")}${modeBtn("AUTO")}</span></h3>
    ${d.live_available ? "" : `<div class="meta">🔒 ${esc(t("web.bot.live_locked"))}</div>`}
    ${d.mode === "CONFIRM" ? `<div class="meta c-accent">${esc(t("web.bot.confirm_note"))}</div>` : ""}
    <div class="kgrid">
      ${kpi(t("web.bot.running"), d.live ? "RUNNING" : "STOPPED", d.live ? "c-green" : "c-red", esc(d.ticks + " ticks"))}
      ${kpi(t("web.bot.balance"), esc(money(s.equity)), "", esc(t("web.bot.from", { v: money(s.starting) })))}
      ${kpi("LEGACY P&L (ref)", esc(money(s.net_pnl, true)), pnlCls(s.net_pnl), s.net_pnl_pct === null ? "" : esc((s.net_pnl_pct > 0 ? "+" : "") + s.net_pnl_pct + "% · " + t("web.bot.today") + " " + money(s.today_pnl, true)))}
      ${kpi("Drawdown", esc(s.max_drawdown_pct + "%"), s.max_drawdown_pct >= L.max_drawdown_pct * 0.5 ? "c-orange" : "", "max " + esc(L.max_drawdown_pct + "%"))}
      ${kpi("Win rate", s.win_rate === null ? "—" : esc(s.win_rate + "%"), "", esc(s.wins + "W / " + s.losses + "L"))}
      ${kpi(t("web.bot.open"), esc(s.open + " / " + L.max_open_positions), "", esc(t("web.bot.exposure")) + " " + esc(money(s.exposure)))}
      ${kpi("Risk", esc(riskState), riskState === "KILL" || riskState === "ERROR" ? "c-red" : riskState === "BLOCKED" ? "c-orange" : "c-green", esc(t("web.bot.l.daily")) + " -" + esc(L.max_daily_loss_pct) + "%")}
      ${kpi(t("web.bot.updated"), updSpan(d.last_tick), "", esc((d.tick_ms ?? "—") + " ms/tick"))}
      ${kpi("Expectancy", esc(money(s.expectancy, true)), pnlCls(s.expectancy), "/ trade")}
      ${kpi("Profit factor", esc(s.profit_factor ?? "—"), "", esc(t("web.bot.avg_win_loss")) + " " + esc(money(s.avg_win) + " / " + money(s.avg_loss)))}
      ${kpi(t("web.bot.fees") + " + slip", esc(money(s.fees + s.network_fees + s.slippage_cost)), "", esc(s.failed_trades) + " failed")}
      ${kpi(t("web.bot.trades"), esc(s.closed), "", s.sample_note === "insufficient" ? esc(t("web.bot.sample_short")) : "")}
    </div>
    ${s.cost_stress && s.cost_stress.n ? `<div class="meta">P&L at round-trip cost ${Object.entries(s.cost_stress.levels).map(([k, v]) => `${esc(k)}: ${esc(v.mean_pct)}%/trade (${esc(money(v.total_usd, true))})`).join(" · ")} · gross move ${esc(s.cost_stress.gross_move_mean_pct)}% · modelled cost ${esc(s.cost_stress.modelled_cost_mean_pct)}% · n=${esc(s.cost_stress.n)}</div>` : ""}
    ${s.noquote && (s.noquote.closed || s.noquote.open) ? `<div class="meta">NO_ROUTE / simulated fills excluded from P&L: ${esc(s.noquote.closed)} closed · ${esc(s.noquote.open)} open · ${esc(money(s.noquote.net, true))}</div>` : ""}
    ${pend ? `<h3><span>⏳ ${esc(t("web.bot.pending"))}</span><span>${esc(d.pending.length)}</span></h3>${pend}` : ""}
  </section>

  <section class="b-scan panel" id="bot-scan"><h3><span>🔍 ${esc(t("web.bot.scanning"))}</span><span class="meta">${hc ? `Helius ${esc(hc.used.toLocaleString("en-US"))} / ${esc(hc.daily_budget.toLocaleString("en-US"))} · ${esc(hc.per_hour.toLocaleString("en-US"))}/h · ${esc(t("web.feed.projected"))} ${esc((hc.projected_month / 1e6).toFixed(2))}M / ${esc((hc.monthly_plan / 1e6).toFixed(0))}M${hc.quota_exhausted ? " · ⚠ QUOTA" : ""}` : ""}</span></h3>
    <div class="cnts">${cnt("web.bot.sc.total", sc.total)}${cnt("web.bot.sc.pre", sc.pre_early)}${cnt("web.bot.sc.watch", sc.early_watch)}${cnt("web.bot.sc.signal", sc.early_signal)}${cnt("web.bot.sc.trade", sc.trade_candidates, true)}</div>
    ${d.pipeline ? `<div class="meta">PIPELINE: discovery ${esc(d.pipeline.discovery_per_min ?? "—")}/min · pre-early ${esc(d.pipeline.pre_early_per_min ?? "—")}/min · early-watch ${esc(d.pipeline.early_watch_per_min ?? "—")}/min ·
      WATCH ${esc(d.pipeline.WATCH)} · PENDING-ID ${esc(d.pipeline.PENDING_IDENTITY)} · TRADE ${esc(d.pipeline.TRADE_CANDIDATE)} · REJECT ${esc(d.pipeline.REJECT)}
      ${Object.keys(d.pipeline.reject_reasons || {}).length ? "(" + esc(Object.entries(d.pipeline.reject_reasons).slice(0, 5).map(([k, v]) => k + " " + v).join(", ")) + ")" : ""}
      ${d.pipeline.summary && d.pipeline.summary.most_blocking ? " · " + esc(t("web.bot.most_blocking")) + ": <b>" + esc(d.pipeline.summary.most_blocking) + "</b>" : ""}</div>` : ""}
  </section>

  <section class="b-cands panel" id="bot-candidates"><h3><span>🟢 ${esc(t("web.bot.evaluating"))}</span><span>${esc(all.length)}</span></h3>
    <div class="ctools"><div class="chips">${chip("ALL")}${chip("BUY")}${chip("WATCH")}${chip("PENDING")}${chip("REJECT")}</div>
      <select class="select" id="botsort">${Object.keys(CAND_SORTS).map(sortOpt).join("")}</select></div>
    <div class="ctable">
      <div class="crow chead"><div class="c-tok">CA / Symbol</div><div class="c-n">MC</div><div class="c-n">Age</div><div class="c-n">Opp</div><div class="c-n">Mom</div><div class="c-n">Risk</div><div class="c-n">Conf</div><div class="c-n">Identity</div><div class="c-n">VET</div><div class="c-dec">Decision</div><div class="c-why">WHY</div></div>
      ${page.length ? page.map(candRow).join("") : `<div class="empty">${esc(t("web.bot.no_eval"))}</div>`}
    </div>
    ${pages > 1 ? `<div class="pager"><button class="btn" data-pg="-1"${P.botPage === 0 ? " disabled" : ""}>‹</button><span>${esc(P.botPage + 1)} / ${esc(pages)} · ${esc(list.length)}</span><button class="btn" data-pg="1"${P.botPage >= pages - 1 ? " disabled" : ""}>›</button></div>` : ""}
  </section>

  <section class="b-pos panel" id="bot-positions"><h3><span>💼 POSITIONS</span><span>${esc(d.positions.length)}</span></h3>
    ${d.positions.length ? `<div class="prow head"><span>Token</span><span>Entry</span><span>Now</span><span>Size</span><span>P&amp;L %</span><span>P&amp;L $</span><span>${esc(t("web.bot.held"))}</span><span>Exit</span></div>` + d.positions.map(posRow).join("") : `<div class="muted small">${esc(t("web.bot.no_positions"))}</div>`}
  </section>

  <section class="b-exit panel" id="bot-exit"><h3><span>🚪 EXIT ENGINE</span><span class="meta">${esc(d.positions.length)} ${esc(t("web.bot.watched"))}</span></h3>
    ${d.exit_engine.map((r) => `<div class="exrow"><span class="k">${esc(t("web.bot.exit." + r.key))}</span>
      <span class="r">${esc(r.rule)}</span>
      <span class="st">${r.watching ? `${r.hit ? `<b class="c-red">${esc(r.hit)} HIT</b> ` : ""}${r.near ? `<b class="c-orange">${esc(r.near)} NEAR</b> ` : ""}${r.unknown ? `<span class="c-muted">${esc(r.unknown)} ?</span> ` : ""}${!r.hit && !r.near && !r.unknown ? `<span class="c-green">OK</span>` : ""}` : `<span class="c-muted">${esc(t("web.bot.armed"))}</span>`}</span>
      ${d.positions.length ? `<span class="pp">${d.positions.map((p) => `$${esc(p.symbol)} ${exitCell(p.exit[r.key])}`).join(" ")}</span>` : ""}</div>`).join("")}
  </section>

  <section class="b-act panel" id="bot-activity"><h3><span>📜 ACTIVITY LOG</span><span class="meta">DISCOVER → PRE-EARLY → EARLY WATCH → EARLY SIGNAL → VET → BUY → HOLD → SELL → NET P&amp;L</span></h3>
    <div class="tlw">${timeline || `<div class="muted small">${esc(t("web.bot.no_activity"))}</div>`}</div>
  </section>

  <section class="b-more">
    ${lcSummary(d)}
    ${sampleReport(d)}
    ${truthPanel(d)}
    ${shadowTables(d)}
    ${auditPanel(d)}
    <details class="sec" data-k="bot_more"${S.open.bot_more ? " open" : ""}><summary>${esc(t("web.bot.details"))}</summary><div class="sec-body">
      <div class="panel"><h3><span>${esc(t("web.bot.balance_history"))}</span><span class="${pnlCls(s.net_pnl)}">${esc(money(s.equity))}</span></h3>${areaChart(d.equity_history)}</div>
      <div class="panel"><h3><span>// MESH HEARTBEAT</span><span class="c-accent">${esc(d.ops_per_s)} ops/s</span></h3>${heartbeat(d.live)}</div>
      <div class="mods">${d.modules.map((m, i) => `<button class="mod ${esc(m.status)}" data-mod="${esc(m.key)}"><div class="n"><span>0${i + 1} · ${esc(MOD_NAMES[m.key])}</span><span class="st">${esc(m.status)}</span></div><div class="t">${esc(t("web.bot.mod." + m.key))}</div></button>`).join("")}</div>
      <div class="panel"><h3><span>${esc(t("web.bot.by_setup"))}</span><span></span></h3>${(s.by_setup || []).map((x) => `<div class="kv"><span class="k">${esc(x.setup)}</span><span class="v ${pnlCls(x.net)}">${esc(money(x.net, true))}</span><div class="meta">${esc(x.trades)} trades · win ${esc(x.win_rate)}% · E ${esc(money(x.expectancy, true))}/trade · PF ${esc(x.profit_factor ?? "—")}</div></div>`).join("") || `<div class="muted small">${esc(t("web.bot.no_closed"))}</div>`}</div>
      <div class="panel"><h3><span>${esc(t("web.bot.limits"))}</span><span></span></h3>
        ${[["web.bot.l.risk_trade", L.risk_per_trade_pct + "%"], ["web.bot.l.max_pos", L.max_position_pct + "%"], ["web.bot.l.exposure", L.max_total_exposure_pct + "%"],
           ["web.bot.l.daily", "-" + L.max_daily_loss_pct + "%"], ["web.bot.l.dd", L.max_drawdown_pct + "%"], ["web.bot.l.slip", L.max_slippage_pct + "%"]]
           .map(([k, val]) => `<div class="kv"><span class="k">${esc(t(k))}</span><span class="v">${esc(val)}</span></div>`).join("")}</div>
    </div></details>
    <div class="actions"><button class="btn ${d.kill_switch ? "" : "danger"} wide" id="killbtn">${esc(d.kill_switch ? t("web.bot.kill_release") : t("web.bot.kill_engage"))}</button></div>
    <div class="disclaimer">${esc(t("web.bot.disclaimer"))}</div>
  </section></div>`;
  document.querySelectorAll("[data-evf]").forEach((b) => { b.onclick = () => { P.botFilter = b.dataset.evf; P.botPage = 0; savePrefs(); renderBot(true); }; });
  document.querySelectorAll("[data-pg]").forEach((b) => { b.onclick = () => { P.botPage = (P.botPage || 0) + Number(b.dataset.pg); savePrefs(); renderBot(true); }; });
  const so = $("#botsort"); if (so) so.onchange = () => { P.botSort = so.value; P.botPage = 0; savePrefs(); renderBot(true); };
  document.querySelectorAll("[data-mod]").forEach((b) => { b.onclick = () => { location.hash = "#/bot/" + b.dataset.mod; }; });
  document.querySelectorAll("[data-approve]").forEach((b) => { b.onclick = async () => {
    try { await api("/api/bot/approve/" + encodeURIComponent(b.dataset.approve), { method: "POST" }); toast(t("web.bot.approved")); renderBot(true); }
    catch (e) { toast(t("web.error") + ": " + e.message); } }; });
  document.querySelectorAll("[data-dismiss]").forEach((b) => { b.onclick = async () => {
    try { await api("/api/bot/dismiss/" + encodeURIComponent(b.dataset.dismiss), { method: "POST" }); renderBot(true); } catch (_) { /* ignore */ } }; });
  document.querySelectorAll("[data-mode]").forEach((b) => { b.onclick = async () => {
    try { await api("/api/bot/mode", { method: "POST", body: { mode: b.dataset.mode } }); renderBot(true); }
    catch (e) { toast(t("web.bot.live_locked")); } }; });
  const more = document.querySelector('details[data-k="bot_more"]');
  if (more) more.addEventListener("toggle", () => { S.open.bot_more = more.open; LS.set("openSecs", S.open); });
  const aud = document.querySelector('details[data-k="bot_audit"]');
  if (aud) aud.addEventListener("toggle", () => { S.open.bot_audit = aud.open; LS.set("openSecs", S.open); });
  $("#killbtn").onclick = async () => {
    const engage = !d.kill_switch;
    if (engage && !confirm(t("web.bot.kill_confirm"))) return;
    try { await api("/api/bot/kill", { method: "POST", body: { engaged: engage } }); renderBot(true); } catch (e) { toast(t("web.error") + ": " + e.message); }
  };
}
function auditPanel(d) {
  const A = d.audit;
  if (!A) return "";
  const s = A.stats, nb = A.near || {};
  const row = (k, v) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`;
  const stats = [["Discovery", s.discovery], ["Pre-Early", s.pre_early], ["Early Watch", s.early_watch],
    ["Early Signal TRUE", s.early_true], ["Early Signal UNKNOWN", s.early_unknown], ["Early Signal FALSE (7/7)", s.early_false],
    ["Early FALSE (<7/7 → WATCH)", s.early_false_partial], ["EarlyScore PASS", s.early_score_pass],
    ["OLD candidates", s.old_candidates], ["NEW candidates", s.new_candidates], ["WATCH", s.watch], ["PENDING", s.pending], ["REJECT", s.reject],
    ["Trade Candidate", s.trade_candidate], ["BUY Candidate", s.buy_candidate], ["Jupiter quote OK", s.quote_ok],
    ["BUY executed", s.buy_executed], ["…of which SIMULATED (no quote)", s.buy_simulated_noquote ?? 0], ["BUY skipped", s.buy_skipped]].map(([k, v]) => row(k, v)).join("");
  const blocks = Object.entries(A.blocked_by || {}).filter(([, v]) => v).map(([k, v]) => row(k, v)).join("");
  const skips = Object.entries(s.skips || {}).map(([k, v]) => k + " " + v).join(", ");
  const jq = Object.entries(d.jupiter_quotes || {}).map(([k, v]) => k + " " + v).join(", ");
  const share = Object.entries(nb.gate_share_of_opp_ge_65_pct || {}).slice(0, 5).map(([k, v]) => k + " " + v + "%").join(", ");
  const top = (A.top || []).map((x) => `<tr><td>${esc(x.symbol || "")}</td><td>${esc(x.age_min)}</td><td>${esc(x.opp ?? "—")}</td><td>${esc(x.mom ?? "—")}</td><td>${esc(x.conf ?? "—")}</td><td>${esc(x.early)}</td><td>${esc(x.vet)}</td><td>${esc(x.risk ?? "—")}</td><td>${esc(x.holders ?? "—")}</td><td>${esc(x.liquidity ? "$" + Math.round(x.liquidity / 1e3) + "K" : "—")}</td><td class="small">${esc((x.blocked_by || []).slice(0, 4).join(", "))}</td></tr>`).join("");
  const ev = (A.events || []).slice(0, 12).map((e) => `<div class="small">${esc(new Date(e.ts * 1000).toLocaleTimeString())} · ${esc(e.kind.toUpperCase())} · ${esc(e.symbol || "")} ${esc(e.detail || "")} ${esc(e.reason || "")}</div>`).join("");
  return `<details class="sec" data-k="bot_audit"${S.open.bot_audit === false ? "" : " open"}><summary>AUDIT ${esc(s.window_h)}h — BUY candidate ${esc(s.buy_candidate)} · BUY ${esc(s.buy_executed)} · skipped ${esc(s.buy_skipped)}</summary><div class="sec-body">
    <div class="panel"><h3><span>${esc(t("web.bot.audit_stats"))}</span><span></span></h3>${stats}
      ${skips ? `<div class="meta">BUY skipped: ${esc(skips)}</div>` : ""}${jq ? `<div class="meta">Jupiter quotes: ${esc(jq)}</div>` : ""}</div>
    <div class="panel"><h3><span>BLOCKED_BY</span><span class="meta">${esc(t("web.bot.audit_distinct"))}</span></h3>${blocks}
      <div class="meta">OPP ≥ 65: ${esc(nb.opp_ge_65 ?? 0)} · + Risk PASS: ${esc(nb.opp_ge_65_risk_pass ?? 0)} · VET PASS: ${esc(nb.vet_pass ?? 0)} · Early TRUE: ${esc(nb.early_true ?? 0)}</div>
      ${share ? `<div class="meta">${esc(t("web.bot.audit_share"))}: ${esc(share)}</div>` : ""}
      ${Object.keys(nb.only_one_condition_left || {}).length ? `<div class="meta">${esc(t("web.bot.audit_one_left"))}: ${esc(Object.entries(nb.only_one_condition_left).map(([k, v]) => k + " " + v).join(", "))}</div>` : ""}</div>
    ${ev ? `<div class="panel"><h3><span>CANDIDATE → QUOTE → BUY</span><span></span></h3>${ev}</div>` : ""}
    <div class="panel"><h3><span>TOP 50 OPP</span><span></span></h3><div class="tblw"><table class="tbl"><thead><tr><th>TOKEN</th><th>AGE</th><th>OPP</th><th>MOM</th><th>CONF</th><th>EARLY</th><th>VET</th><th>RISK</th><th>HOLDER</th><th>LIQ</th><th>BLOCKED_BY</th></tr></thead><tbody>${top}</tbody></table></div></div>
  </div></details>`;
}

async function renderBotModule(key) {
  let m;
  try { m = await api("/api/bot/module/" + encodeURIComponent(key)); } catch (e) { $("#view").innerHTML = `<div class="empty">${esc(t("web.error"))}</div>`; return; }
  const items = m.items || [];
  let body = "";
  if (key === "vet") {
    body = items.map((x) => `<div class="panel"><div class="card-head"><a class="sym" href="#/token/${esc(x.mint)}">$${esc(x.symbol)}</a>
        <span class="badge ${x.decision === "TRADE" ? "b-valid" : x.decision === "WATCH" ? "b-partial" : "b-invalid"}">${x.decision === "TRADE" ? "🟢" : x.decision === "WATCH" ? "🟡" : "🔴"} ${esc(x.decision)}</span></div>
      <div class="meta">Opportunity ${esc(x.opportunity ?? "—")} · Confidence ${esc(x.confidence)} · ${esc(Object.entries(x.components).map(([k, v]) => k + " " + (v ?? "N/A")).join(" · "))}</div>
      ${x.checks.map((c) => `<div class="chk"><span class="${c.result === "PASS" ? "c-green" : c.result === "N/A" ? "c-muted" : "c-red"}">${c.result === "PASS" ? "✓" : c.result === "N/A" ? "–" : c.result === "UNKNOWN" ? "?" : "✗"}</span>
        <span><b>${esc(t("web.bot.chk." + c.key))}</b> ${esc(c.value)}<div class="meta">${esc(c.rule)}</div></span></div>`).join("")}
      <div class="meta"><b>Why:</b> ${esc(x.why.join(" · "))}</div><div class="meta"><b>${esc(t("web.bot.invalidate"))}:</b> ${esc(x.invalidate.join(" · "))}</div></div>`).join("");
  } else if (key === "fills") {
    body = items.map((x) => `<div class="kv"><span class="k">${esc(new Date(x.ts * 1000).toLocaleTimeString("vi-VN"))} ${esc(x.side)} $${esc(x.symbol)} · ${esc(x.status)}</span>
      <span class="v">${esc(money(x.usd))}</span><div class="meta">${esc(x.route)} · impact ${esc(x.impact)}% · slip ${esc(x.slip)}% · fee ${esc(money(x.fee))} · ${esc(x.latency)} ms${x.reason ? " · " + esc(x.reason) : ""}</div></div>`).join("");
  } else if (key === "book") {
    body = items.map(posHtml).join("");
  } else {
    body = items.map((x) => `<div class="kv"><span class="k">$${esc(x.symbol || "")}</span><span class="v">${x.usd !== undefined ? esc(money(x.usd)) : x.allowed !== undefined ? (x.allowed ? "✓" : "✗") : ""}</span>
      <div class="meta">${esc((x.why || x.reasons || []).join(" · "))}</div></div>`).join("");
  }
  $("#view").innerHTML = `<button class="back" id="back">‹ ${esc(t("web.back"))}</button>
    <div class="bot-head"><h1>${esc(MOD_NAMES[key] || key)}</h1><span class="badge">${esc(m.status)}</span></div>
    <div class="meta">${esc(m.detail)} · ${updSpan(m.updated)}</div>${body || `<div class="empty">${esc(t("common.none"))}</div>`}`;
  $("#back").onclick = () => { location.hash = "#/bot"; };
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
      refreshStatus(); syncWatch(); if (!location.hash) location.hash = "#/home"; route();
    }
    catch (_) { /* message shown by api() */ }
  };
  $("#go").onclick = go;
  $("#code").onkeydown = (e) => { if (e.key === "Enter") go(); };
}

/* ---------------------------------------------------------------- insiders (call channel scan) */
function acct(a) {
  return `<a class="mono" href="https://solscan.io/account/${esc(a)}" target="_blank" rel="noopener">${esc(short(a))}</a> <span class="copy" data-copy="${esc(a)}">⧉</span>`;
}
function tks(list, names) { return (list || []).map((m) => esc(names[m] || short(m))).join(", "); }
function insTable(head, rows) {
  return rows.length ? `<div class="ins-wrap"><table class="ins"><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`
    : `<div class="empty">${esc(t("common.none"))}</div>`;
}
async function renderInsiders(silent) {
  const view = $("#view");
  if (!silent) view.innerHTML = `<h1>${esc(t("web.title.insiders"))}</h1><div id="ins"><div class="spin">${esc(t("web.loading"))}</div></div>`;
  let d;
  try { d = await api("/api/insiders"); } catch (e) { if (!silent) $("#ins").textContent = t("web.error"); return; }
  S.insRunning = d.running;
  const r = d.result, names = (r && r.tickers) || {};
  const runBtn = d.can_run ? `<button class="btn" id="insRun" ${d.running ? "disabled" : ""}>${esc(d.running ? t("web.ins.running") : t("web.ins.run"))}</button>` : `<span class="c-muted small">${esc(t("web.ins.no_key"))}</span>`;
  const prog = d.running || d.error ? `<div class="muted small mono">${esc((d.progress || []).slice(-4).join(" · "))}</div>${d.error ? `<div class="c-red small">${esc(d.error)}</div>` : ""}` : "";
  if (!r) { $("#ins").innerHTML = `<div class="token-head">${runBtn}${prog}</div><div class="empty">${esc(t("web.ins.empty"))}</div>`; bindIns(); return; }
  const toks = r.tokens || [], okT = toks.filter((x) => x.status === "ok");
  const stat = {}; toks.forEach((x) => { const k = x.status.startsWith("error") ? "error" : x.status; stat[k] = (stat[k] || 0) + 1; });
  const ents = r.entities || [];
  const real = ents.filter((e) => !e.hub), hubs = ents.filter((e) => e.hub);
  const ev = (e) => (e.evidence || []).length ? `<details class="ev"><summary>${esc(t("web.ins.evidence"))} (${e.evidence.length})</summary>${e.evidence.map((x) =>
    `<div class="small">${acct(x.wallet)} ${x.from_sol ? "← " + x.from_sol + " SOL" : ""}${x.in_sol ? "→ " + x.in_sol + " SOL" : ""} · ${(x.roles || []).map((r) =>
      esc(names[r[0]] || short(r[0])) + " " + (r[1] === "deployer" ? esc(t("web.ins.deployer")) : "#" + r[1]) + (r[2] ? "" : " (" + esc(t("web.ins.after")) + ")")).join(", ")}</div>`).join("")}</details>` : "";
  const entRow = (e) => `<tr><td>${acct(e.address)}${e.label ? ` <span class="pill ${e.kind === "exchange" ? "" : "ok"}">${esc(e.label)}</span>` : ""}${e.is_insider_wallet ? ` <span class="pill">${esc(t("web.ins.also_insider"))}</span>` : ""}</td><td class="num"><b>${e.n_tokens}</b></td>
    <td class="num">${e.funded_wallets} / ${e.funded_sol}</td><td class="num">${e.received_from_wallets} / ${e.received_sol}</td><td class="small">${tks(e.tokens, names)}${ev(e)}</td></tr>`;
  const ehead = [t("web.ins.addr"), t("web.ins.n_tokens"), t("web.ins.funded"), t("web.ins.received"), t("web.ins.tokens")];
  // n_wallets >= 2 is applied by the server
  $("#ins").innerHTML = `<div class="token-head">
      <div class="kv"><span class="k">${esc(t("web.ins.scanned"))}</span><span class="v">${okT.length} / ${toks.length} (${esc(Object.entries(stat).map(([k, v]) => k + " " + v).join(", "))})</span></div>
      <div class="kv"><span class="k">${esc(t("web.ins.generated"))}</span><span class="v">${esc(r.generated_at)} · RPC ${r.rpc_calls} · ${esc(t("web.ins.traced"))} ${r.traced_wallets}</span></div>
      ${runBtn}${prog}</div>
    <div class="disclaimer">${esc(t("web.ins.how"))}</div>
    ${deepHtml(r.deep, names)}
    <h2>${esc(t("web.ins.behind"))}</h2>${insTable(ehead, real.slice(0, 40).map(entRow))}
    <h2>${esc(t("web.ins.hop2"))}</h2>${insTable([t("web.ins.addr"), t("web.ins.n_tokens"), t("web.ins.children"), t("web.ins.tokens")],
      (r.hop2_parents || []).slice(0, 25).map((p) => `<tr><td>${acct(p.address)}</td><td class="num"><b>${p.n_tokens}</b></td><td class="num">${p.n_children}</td><td class="small">${tks(p.tokens, names)}</td></tr>`))}
    <h2>${esc(t("web.ins.repeat"))}</h2>${insTable([t("web.ins.addr"), t("web.ins.n_tokens"), t("web.ins.pre_call"), t("web.ins.deployer"), t("web.ins.tokens")],
      (r.repeat_wallets || []).slice(0, 60).map((w) => `<tr><td>${acct(w.wallet)}${w.bot_like ? ` <span class="pill warn">${esc(t("web.ins.bot"))}</span>` : ""}</td><td class="num"><b>${w.n_tokens}</b></td><td class="num">${w.n_pre_call}</td><td class="small">${tks(w.deployer_of, names)}</td><td class="small">${tks(w.tokens, names)}</td></tr>`))}
    <h2>${esc(t("web.ins.hubs"))}</h2><div class="muted small">${esc(t("web.ins.hubs_note"))}</div>${insTable(ehead, hubs.slice(0, 30).map(entRow))}
    <h2>${esc(t("web.ins.token_list"))}</h2>${insTable([t("web.ins.token"), t("web.ins.call"), t("web.ins.created"), t("web.ins.deployer"), t("web.ins.buyers")],
      toks.map((x) => `<tr><td>${esc(x.ticker || short(x.mint))} <span class="copy" data-copy="${esc(x.mint)}">⧉</span></td><td class="small">${esc(new Date(x.call_ts * 1000).toLocaleString("vi-VN"))}</td>
        <td class="small">${x.created_ts ? esc(new Date(x.created_ts * 1000).toLocaleString("vi-VN")) : esc(x.status)}</td><td>${x.deployer ? acct(x.deployer) : "—"}</td>
        <td class="num">${x.n_buyers ?? 0} (${x.n_pre_call ?? 0} ${esc(t("web.ins.before"))})</td></tr>`))}`;
  bindIns();
}
function moneyPath(p) {
  // p: [{from, to, dir, sol}] from the address back to a seed; print each hop in the direction the SOL moved
  return p.map((h) => h.dir === "back" ? `${esc(short(h.to))} → ${esc(short(h.from))}` : `${esc(short(h.from))} → ${esc(short(h.to))}`)
    .map((x, i) => x + ` <span class="c-muted">(${p[i].sol} SOL)</span>`).join(" · ");
}
function deepHtml(d, names) {
  if (!d) return "";
  const lab = (x) => (x.label ? ` <span class="pill ${x.kind === "exchange" ? "" : "ok"}">${esc(x.label)}</span>` : "") +
    (x.pays_into ? ` <span class="pill warn">${esc(t("web.ins.pays_into"))} ${esc(x.pays_into)}</span>` : "");
  const hops = (x) => [x.hop_back !== null && x.hop_back !== undefined ? "←" + x.hop_back : "", x.hop_fwd ? "→" + x.hop_fwd : ""].filter(Boolean).join(" ");
  const rows = (d.top || []).slice(0, 60).map((x) => `<tr><td>${acct(x.address)}${lab(x)}</td><td class="num"><b>${x.n_tokens}</b></td>
    <td class="num">${esc(hops(x))}</td><td class="num">${x.sol_out} / ${x.sol_in}</td>
    <td class="small">${tks(x.tokens, names)}<details class="ev"><summary>${esc(t("web.ins.paths"))}</summary>${(x.paths || []).map((p) =>
      `<div class="small"><b>${esc(names[p.token] || short(p.token))}</b>: ${moneyPath(p.path)}</div>`).join("")}</details></td></tr>`);
  return `<h2>${esc(t("web.ins.deep"))}</h2><div class="muted small">${esc(t("web.ins.deep_note", { hops: d.hops, n: d.reached }))}${d.from_earlier_scan ? " · " + esc(t("web.ins.deep_old")) : ""}</div>
    ${insTable([t("web.ins.addr"), t("web.ins.n_tokens"), t("web.ins.hops"), t("web.ins.sol_out_in"), t("web.ins.tokens")], rows)}`;
}
function bindIns() {
  document.querySelectorAll("table.ins").forEach((tb) => {   // label each cell for the narrow-screen card layout
    const hs = [...tb.querySelectorAll("th")].map((h) => h.textContent);
    tb.querySelectorAll("tbody tr").forEach((tr) => [...tr.children].forEach((td, i) => { td.dataset.l = hs[i] || ""; }));
  });
  const b = $("#insRun");
  if (b) b.onclick = async () => { b.disabled = true; try { await api("/api/insiders/run", { method: "POST" }); toast(t("web.ins.started")); } catch (e) { toast(t("web.error")); } renderInsiders(true); };
}

/* ---------------------------------------------------------------- router + polling */
function stopPoll() { clearInterval(S.timer); S.timer = null; }
function startPoll(fn, ms) {
  stopPoll();
  let n = 0;
  S.timer = setInterval(() => {
    if (document.hidden) return;
    if (n++ % 2 === 0) refreshStatus();         // status pills every other tick
    fn();
  }, ms || POLL.list);
}
function setTab(tab) { document.querySelectorAll(".tabbar a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab)); }
function route() {
  if (!S.code) { showLogin(""); return; }
  let h = location.hash || "#/home";
  if (/^#[a-z]/.test(h)) h = "#/" + h.slice(1);              // "#bot" -> "#/bot"
  const parts = h.slice(2).split("/");
  $("#view").classList.remove("bot-wide");
  window.scrollTo(0, 0);
  const [p0, p1] = parts;
  if (p0 === "bot" && p1) { setTab("bot"); renderBotModule(p1); startPoll(() => renderBotModule(p1), POLL.bot); return; }
  if (p0 === "bot") { setTab("bot"); renderBot(); startPoll(() => renderBot(true), POLL.bot); return; }
  if (p0 === "home") { setTab("home"); renderHome(); startPoll(() => renderHome(true), POLL.home); return; }
  if (p0 === "token" && p1) { setTab(""); renderToken(p1); startPoll(() => renderToken(p1, true), POLL.token); return; }
  if (p0 === "early") { setTab("early"); renderTiers(p1); startPoll(() => renderTiers(p1 || S.prefs.tier, true), POLL.home); return; }
  if (p0 === "new") { setTab(p0); renderList(p0); startPoll(() => renderList(p0, true), POLL.list); return; }
  if (p0 === "top") { setTab("more"); renderList(p0); startPoll(() => renderList(p0, true), POLL.list); return; }
  if (p0 === "list" && ["whales", "dev", "social"].includes(p1)) { setTab("more"); renderList(p1); startPoll(() => renderList(p1, true), POLL.list); return; }
  if (p0 === "watch") { setTab("watch"); renderWatch(); startPoll(() => renderWatch(true), POLL.watch); return; }
  if (p0 === "narrative") { setTab("more"); renderNarrative(); stopPoll(); return; }
  if (p0 === "events") { setTab("more"); renderEvents(); startPoll(() => renderEvents(true), POLL.events); return; }
  if (p0 === "insiders") { setTab("more"); renderInsiders(); startPoll(() => { if (S.insRunning) renderInsiders(true); }, 5000); return; }
  if (p0 === "status") { setTab("more"); renderStatus(); startPoll(() => renderStatus(true), POLL.status); return; }
  if (p0 === "smart") { setTab("more"); $("#view").innerHTML = `<h1>${esc(t("tab.smart_money"))}</h1><div class="token-head">${esc(t("na.smart_money"))}</div><div class="disclaimer">${esc(t("na.rule"))}</div>`; stopPoll(); return; }
  setTab("more"); renderMore(); stopPoll();
}

document.addEventListener("click", (e) => {
  const cp = e.target.closest("[data-copy]");
  if (cp) { e.preventDefault(); e.stopPropagation(); copyText(cp.dataset.copy); return; }
  const star = e.target.closest("[data-star]");
  if (star) {
    e.preventDefault(); e.stopPropagation();
    toggleWatch(star.dataset.star).then(() => { star.classList.toggle("on", isWatched(star.dataset.star)); star.textContent = isWatched(star.dataset.star) ? "♥" : "♡"; });
  }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.code) { refreshStatus(); } });
window.addEventListener("hashchange", route);
setInterval(tickUpd, 1000);            // "Cập nhật Xs trước" counts up without refetching

(async function init() {
  try { S.dict = await (await fetch("/i18n/vi.json", { cache: "no-cache" })).json(); } catch (_) { S.dict = {}; }
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  if (!S.code) { showLogin(""); return; }
  refreshStatus(); syncWatch(); route();
})();
