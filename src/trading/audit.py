"""Rolling run audit (default 4 h) — READ-ONLY: it records what the bot saw and why nothing was bought; it decides
nothing. One compact record per token (its best-Opportunity snapshot + every gate that ever blocked it), plus the
execution funnel (BUY candidate -> Jupiter quote -> BUY / SKIP). Persisted to JSON so a restart keeps the window.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

WINDOW_S = 4 * 3600
MAX_TOKENS = 30000
TRADE_MIN_OPP = 65

# blocked_by entry -> owner-facing category
def category(b: str) -> str:
    k = b.split(":", 1)[1].strip() if ":" in b and b.startswith(("vet", "risk_engine")) else ""
    head = b.split(":", 1)[0]
    if head in ("identity_conflict", "identity_pending"):
        return "identity"
    if head.startswith("early"):
        return "early_signal"
    if head in ("risk", "risk_unknown", "risk_engine"):
        return "risk"
    if head in ("liquidity", "liquidity_unknown"):
        return "liquidity"
    if head in ("opportunity", "opportunity_unknown"):
        return "opportunity"
    if head == "confidence":
        return "confidence"
    if head in ("vet", "vet_unknown"):
        if k == "volume_buy_pressure":
            return "volume_buy_pressure"
        if k in ("holders", "top_holders"):
            return "holder"
        return "vet"
    if head == "jupiter":
        return "jupiter_quote"
    return "other"


CATEGORIES = ("volume_buy_pressure", "holder", "liquidity", "early_signal", "vet", "risk", "opportunity",
              "confidence", "identity", "jupiter_quote", "other")


class RunAudit:
    def __init__(self, path: str | Path | None = None, window_s: float = WINDOW_S):
        self.path = Path(path) if path else None
        self.window_s = window_s
        self.started = time.time()
        self.tokens: dict[str, dict] = {}
        self.funnel = {"buy_candidate": 0, "quote_ok": 0, "buy_executed": 0, "buy_skipped": 0}
        self.skips: dict[str, int] = {}          # "jupiter:NO_ROUTE", "risk_at_execution", ...
        self.events: list[dict] = []             # last execution events (Candidate -> Quote -> BUY / SKIP)
        self._saved = 0.0
        self.load()

    # ------------------------------------------------------------ recording
    def _tok(self, mint: str, now: float) -> dict:
        t = self.tokens.get(mint)
        if t is None:
            if len(self.tokens) >= MAX_TOKENS:
                oldest = min(self.tokens, key=lambda m: self.tokens[m]["last"])
                self.tokens.pop(oldest, None)
            t = self.tokens[mint] = {"first": now, "last": now, "pre": False, "watch": False, "early": "UNKNOWN",
                                     "decision": None, "ever": [], "cand": False, "blocks": [], "best": None}
        t["last"] = now
        return t

    def observe_stage(self, st, now: float) -> None:
        t = self._tok(st.mint, now)
        pe, ew = getattr(st, "pre_early", None), getattr(st, "early_watch", None)
        if pe is not None and pe.status != "NOT_ELIGIBLE":
            t["pre"] = True
        if ew is not None and ew.eligible:
            t["watch"] = True
        es = st.early
        if es is not None and es.strength is not None:
            if es.is_early is True:
                t["early"] = "TRUE"
            elif t["early"] != "TRUE":
                t["early"] = "FALSE" if es.groups_computable >= 7 else "FALSE_PARTIAL"

    def observe_decision(self, st, rec: dict, now: float, candidate: bool) -> None:
        t = self._tok(st.mint, now)
        d = "TRADE_CANDIDATE" if candidate else rec.get("decision")
        t["decision"] = d
        if d not in t["ever"]:
            t["ever"].append(d)
        t["cand"] = t["cand"] or candidate
        cats = sorted({category(b) for b in rec.get("blocked_by") or []})
        for c in cats:
            if c not in t["blocks"]:
                t["blocks"].append(c)
        opp = rec.get("opportunity")
        best = t["best"]
        if best is None or (opp is not None and (best["opp"] is None or opp >= best["opp"])):
            comp = rec.get("components") or {}
            checks = {c["key"]: c for c in rec.get("checks") or []}
            vet_fail = [k for k, c in checks.items() if c["result"] == "FAIL"]
            vet_unk = [k for k, c in checks.items() if c["result"] == "UNKNOWN"]
            es = st.early
            m = st.market
            h = st.holders if getattr(st, "holder_status", None) == "ok" else None
            t["best"] = {
                "symbol": st.info.symbol, "ts": now,
                "age_min": round((now - (st.info.created_at or st.info.discovered_at or now)) / 60, 1),
                "opp": opp, "mom": comp.get("momentum"), "conf": rec.get("confidence"),
                "early": ("TRUE" if es and es.is_early is True else "UNKNOWN" if es is None or es.strength is None
                          else f"FALSE {es.groups_computable}/7"),
                "identity": st.identity.status,
                "vet": "PASS" if rec.get("vet_passed") else (f"FAIL {','.join(vet_fail[:3])}" if vet_fail
                                                            else f"UNKNOWN {','.join(vet_unk[:3])}"),
                "risk": st.risk.score if st.risk else None,
                "holders": h.holder_count if h else None,
                "liquidity": m.liquidity_usd if m else None,
                "decision": d, "blocked_by": (rec.get("blocked_by") or [])[:8], "categories": cats}

    def execution(self, kind: str, st, now: float, detail: str = "", reason: str = "") -> None:
        """kind: candidate | quote_ok | buy | skip | retry"""
        if kind == "candidate":
            self.funnel["buy_candidate"] += 1
        elif kind == "quote_ok":
            self.funnel["quote_ok"] += 1
        elif kind == "buy":
            self.funnel["buy_executed"] += 1
        elif kind == "skip":
            self.funnel["buy_skipped"] += 1
            self.skips[reason] = self.skips.get(reason, 0) + 1
            if reason.startswith("jupiter") and st is not None:
                t = self._tok(st.mint, now)
                if "jupiter_quote" not in t["blocks"]:
                    t["blocks"].append("jupiter_quote")
        self.events.append({"ts": now, "kind": kind, "mint": st.mint if st else "", "symbol": st.info.symbol if st else "",
                            "detail": detail, "reason": reason})
        del self.events[:-200]

    # ------------------------------------------------------------ report
    def report(self, now: float | None = None, top: int = 50) -> dict:
        now = now or time.time()
        live = {m: t for m, t in self.tokens.items() if now - t["last"] <= self.window_s}
        n = lambda f: sum(1 for t in live.values() if f(t))  # noqa: E731
        stats = {
            "window_h": round(min(self.window_s, now - self.started) / 3600, 2),
            "discovery": len(live), "pre_early": n(lambda t: t["pre"]), "early_watch": n(lambda t: t["watch"]),
            "early_true": n(lambda t: t["early"] == "TRUE"), "early_unknown": n(lambda t: t["early"] == "UNKNOWN"),
            "early_false": n(lambda t: t["early"] == "FALSE"), "early_false_partial": n(lambda t: t["early"] == "FALSE_PARTIAL"),
            "watch": n(lambda t: t["decision"] == "WATCH"), "pending": n(lambda t: t["decision"] == "PENDING_IDENTITY"),
            "reject": n(lambda t: t["decision"] == "REJECT"), "trade_candidate": n(lambda t: t["cand"]),
            **self.funnel, "skips": dict(sorted(self.skips.items(), key=lambda x: -x[1])),
        }
        blocked = {c: n(lambda t, c=c: c in t["blocks"]) for c in CATEGORIES}
        bests = [t["best"] | {"mint": m} for m, t in live.items() if t["best"]]
        bests.sort(key=lambda b: (b["opp"] is None, -(b["opp"] or 0), -(b["conf"] or 0)))
        hi = [b for b in bests if b["opp"] is not None and b["opp"] >= TRADE_MIN_OPP]
        one_left: dict[str, int] = {}
        for b in bests:
            if len(b["categories"]) == 1:
                one_left[b["categories"][0]] = one_left.get(b["categories"][0], 0) + 1
        gate_share = {c: round(100 * sum(1 for b in hi if c in b["categories"]) / len(hi), 1) for c in CATEGORIES} if hi else {}
        near = {
            "opp_ge_65": len(hi),
            "opp_ge_65_risk_pass": sum(1 for b in hi if "risk" not in b["categories"]),
            "vet_pass": sum(1 for b in bests if b["vet"] == "PASS"),
            "early_true": sum(1 for b in bests if b["early"] == "TRUE"),
            "only_one_condition_left": dict(sorted(one_left.items(), key=lambda x: -x[1])),
            "gate_share_of_opp_ge_65_pct": dict(sorted(((k, v) for k, v in gate_share.items() if v), key=lambda x: -x[1])),
        }
        return {"stats": stats, "blocked_by": dict(sorted(blocked.items(), key=lambda x: -x[1])), "near": near,
                "top": bests[:top], "events": self.events[-40:][::-1]}

    # ------------------------------------------------------------ persistence
    def save(self, now: float | None = None, force: bool = False) -> None:
        now = now or time.time()
        if not self.path or (not force and now - self._saved < 60):
            return
        self._saved = now
        live = {m: t for m, t in self.tokens.items() if now - t["last"] <= self.window_s}
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"started": self.started, "tokens": live, "funnel": self.funnel,
                                       "skips": self.skips, "events": self.events[-200:]}), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        now = time.time()
        self.tokens = {m: t for m, t in (d.get("tokens") or {}).items() if now - t.get("last", 0) <= self.window_s}
        if self.tokens:
            self.started = max(float(d.get("started") or now), now - self.window_s)
            self.funnel.update(d.get("funnel") or {})
            self.skips = d.get("skips") or {}
            self.events = d.get("events") or []
