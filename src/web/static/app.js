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
  if (!silent) view.innerHTML = `<h1>${esc(t("web.title." + kind))}</h1>${toolbarHtml(kind, p)}<div id="list"><div class="spin">${esc(t("web.loading"))}</div></div>`;
  bindToolbar(kind);
  try {
    const data = await api(`/api/list/${kind}?limit=300`);
    syncClock(data.server_time);
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
async function renderBot(silent) {
  let d;
  try { d = await api("/api/bot"); } catch (e) { if (!silent) $("#view").innerHTML = `<div class="empty">${esc(t("web.error"))}: ${esc(e.message)}</div>`; return; }
  syncClock(d.server_time);
  const s = d.stats;
  const kpi = (k, v, sub, cls) => `<div class="kpi"><div class="k">${esc(k)}</div><div class="v ${cls || ""}">${v}</div><div class="s">${sub}</div></div>`;
  const mods = d.modules.map((m, i) => `<button class="mod ${esc(m.status)}" data-mod="${esc(m.key)}">
      <div class="n"><span>0${i + 1} · ${esc(MOD_NAMES[m.key])}</span><span class="st">${esc(m.status)}</span></div>
      <div class="t">${esc(t("web.bot.mod." + m.key))}</div></button>`).join("");
  const acts = d.activity.length ? d.activity.slice(0, 40).map((a) => `<div class="row"><span class="c-muted">${esc(new Date(a.ts * 1000).toLocaleTimeString("vi-VN"))}</span>
      <span class="${esc(a.kind)}">${a.kind === "BUY" ? "▲ " : a.kind === "SELL" ? "▼ " : ""}${esc(a.kind)}</span>
      <span>${a.symbol ? "$" + esc(a.symbol) + " " : ""}${a.usd !== null && a.usd !== undefined ? esc(money(a.usd)) + " · " : ""}${esc(a.text)}</span></div>`).join("")
    : `<div class="muted small">${esc(t("web.bot.no_activity"))}</div>`;
  const st = (k, v, cls) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v ${cls || ""}">${v}</span></div>`;
  const L = d.limits;
  $("#view").innerHTML = `
    <div class="bot-head"><h1>SOL TRADING BOT</h1><span class="paper">${esc(d.mode)}</span>
      <span class="live${d.live ? "" : " off"}"><i></i>${d.live ? "LIVE" : "OFFLINE"}</span></div>
    <div class="banner orange">${esc(t("web.bot.paper_note"))}</div>
    <div class="kpis">
      ${kpi(t("web.bot.balance"), esc(money(s.equity)), esc(t("web.bot.from", { v: money(s.starting) })))}
      ${kpi(t("web.bot.pnl_total"), esc(money(s.net_pnl, true)), (s.net_pnl_pct === null ? "" : esc((s.net_pnl_pct > 0 ? "+" : "") + s.net_pnl_pct + "%")) + " · " + esc(t("web.bot.today")) + " " + esc(money(s.today_pnl, true)), pnlCls(s.net_pnl))}
      ${kpi(t("web.bot.winrate"), s.win_rate === null ? "—" : esc(s.win_rate + "%"), esc(s.wins + "W / " + s.losses + "L"))}
      ${kpi(t("web.bot.open"), esc(s.open + " / " + L.max_open_positions), esc(t("web.bot.exposure")) + " " + esc(money(s.exposure)))}
    </div>
    <div class="kpis">
      ${kpi(t("web.bot.risk"), esc(d.risk_state), d.kill_switch ? esc(t("web.bot.kill_on")) : esc(t("web.bot.max_dd")) + " " + esc(s.max_drawdown_pct + "%"), d.kill_switch || d.risk_state === "ERROR" ? "c-red" : d.risk_state === "BLOCKED" ? "c-orange" : "c-green")}
      ${kpi(t("web.bot.updated"), updSpan(d.last_tick), esc(d.ticks + " ticks · " + (d.tick_ms ?? "—") + " ms"))}
    </div>
    <div class="panel"><h3><span>${esc(t("web.bot.balance_history"))}</span><span class="${pnlCls(s.net_pnl)}">${esc(money(s.equity))}</span></h3>${areaChart(d.equity_history)}</div>
    <div class="panel"><h3><span>// MESH HEARTBEAT</span><span class="c-accent">${esc(d.ops_per_s)} ops/s</span></h3>${heartbeat(d.live)}</div>
    <div class="mods">${mods}</div>
    <div class="panel"><h3><span>${esc(t("web.bot.positions"))}</span><span>${esc(d.positions.length)}</span></h3>
      ${d.positions.length ? d.positions.map(posHtml).join("") : `<div class="muted small">${esc(t("web.bot.no_positions"))}</div>`}</div>
    <div class="panel"><h3><span>ACTIVITY LOG</span><span>${esc(d.activity.length)}</span></h3><div class="log">${acts}</div></div>
    <div class="panel"><h3><span>NET P&amp;L</span><span></span></h3>
      ${st(t("web.bot.gross"), esc(money(s.gross_pnl, true)), pnlCls(s.gross_pnl))}${st(t("web.bot.fees"), esc(money(-s.fees)))}
      ${st(t("web.bot.network_fees"), esc(money(-s.network_fees)))}${st(t("web.bot.slippage"), esc(money(-s.slippage_cost)))}
      ${st(t("web.bot.failed"), esc(s.failed_trades + " · " + money(-s.failed_fees)))}${st("NET P&L", esc(money(s.net_pnl, true)), pnlCls(s.net_pnl))}
      ${st(t("web.bot.avg_win_loss"), esc(money(s.avg_win) + " / " + money(s.avg_loss)))}${st("Profit factor", esc(s.profit_factor ?? "—"))}
      ${st("Max drawdown", esc(s.max_drawdown_pct + "%"))}</div>
    <div class="panel"><h3><span>${esc(t("web.bot.limits"))}</span><span></span></h3>
      ${st(t("web.bot.l.risk_trade"), esc(L.risk_per_trade_pct + "%"))}${st(t("web.bot.l.max_pos"), esc(L.max_position_pct + "%"))}
      ${st(t("web.bot.l.exposure"), esc(L.max_total_exposure_pct + "%"))}${st(t("web.bot.l.daily"), esc("-" + L.max_daily_loss_pct + "%"))}
      ${st(t("web.bot.l.dd"), esc(L.max_drawdown_pct + "%"))}${st(t("web.bot.l.slip"), esc(L.max_slippage_pct + "%"))}
      ${st(t("web.bot.l.liq"), esc(money(L.min_liquidity_usd)))}${st("SL / TP1 / TP2 / Trail", esc(`-${L.stop_loss_pct}% / +${L.tp1_pct}% / +${L.tp2_pct}% / ${L.trailing_pct}%`))}
      ${st(t("web.bot.sources"), esc("X Alpha: " + t("common.not_available") + " · Smart money: " + t("common.not_available")))}
      <div class="actions two"><button class="btn ${d.kill_switch ? "" : "danger"}" id="killbtn">${esc(d.kill_switch ? t("web.bot.kill_release") : t("web.bot.kill_engage"))}</button>
      <button class="btn" disabled>${esc(t("web.bot.auto_locked"))}</button></div></div>
    <div class="disclaimer">${esc(t("web.bot.disclaimer"))}</div>`;
  document.querySelectorAll("[data-mod]").forEach((b) => { b.onclick = () => { location.hash = "#/bot/" + b.dataset.mod; }; });
  $("#killbtn").onclick = async () => {
    const engage = !d.kill_switch;
    if (engage && !confirm(t("web.bot.kill_confirm"))) return;
    try { await api("/api/bot/kill", { method: "POST", body: { engaged: engage } }); renderBot(true); } catch (e) { toast(t("web.error") + ": " + e.message); }
  };
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
  const h = location.hash || "#/home";
  const parts = h.slice(2).split("/");
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
