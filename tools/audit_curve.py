"""Bonding-curve audit (read-only): runs the real scanner for N minutes and reports, for every token that was
observed inside the PRE-EARLY window (0.5–3 min old):

  Discovered -> in window -> curve valid / curve invalid / curve missing / AMM pair / no market -> rejected

plus the top reasons a curve was rejected with sample CAs, and the raw Pump.fun fields of those samples
(curve address, quote mint, virtual/real reserves, mayhem flag, complete) so every reason can be checked by hand.

usage: python tools/audit_curve.py --minutes 20 [--out audit.json]
Never prints API keys (HELIUS_API_KEY is read from the environment only).
"""
import argparse
import asyncio
import collections
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.config import ApiKeys, Settings  # noqa: E402
from database.db import Database  # noqa: E402
from scanner.engine import ScannerEngine  # noqa: E402

CURVE_KEYS = {"curve_unavailable", "curve_stale", "curve_inconsistent", "curve_too_small", "sol_price_missing",
              "curve_small", "curve_empty", "curve_graduated", "curve_malformed"}
VALID_NOTED = {"curve_small"}                      # usable reserve, only a note
MISSING = {"curve_unavailable", "curve_stale", "sol_price_missing", "curve_empty", "curve_graduated"}
# curve_inconsistent / curve_too_small / curve_malformed: "invalid" when CRITICAL (old rule), "unknown" when a warning


def classify(st) -> tuple[str, str | None, dict]:
    m = st.market
    if m is None:
        return "no_market", None, {}
    curve_issue = next((i for i in st.market_issues if i.key in CURVE_KEYS), None)
    if not m.is_curve:
        return "amm", None, {}
    if curve_issue is None:
        return "curve_valid", None, {}
    if curve_issue.key in VALID_NOTED:
        return "curve_valid", curve_issue.key, dict(curve_issue.params)
    if curve_issue.severity == "critical" and curve_issue.key not in MISSING:
        return "curve_invalid", curve_issue.key, dict(curve_issue.params)
    return "curve_missing", curve_issue.key, dict(curve_issue.params)


async def run(minutes: float) -> dict:
    eng = ScannerEngine(Settings(), Database(Path(tempfile.mkdtemp()) / "audit.db"), keys=ApiKeys.from_env(),
                        on_log=lambda m: None)
    seen: dict[str, dict] = {}
    task = asyncio.create_task(eng.run())
    end = time.time() + minutes * 60
    while time.time() < end:
        await asyncio.sleep(10)
        now = time.time()
        for st in list(eng.tracked.values()):
            rec = seen.setdefault(st.mint, {"mint": st.mint, "symbol": st.info.symbol, "sources": sorted(st.info.sources),
                                            "window": [], "first_seen": now})
            age = st.age_minutes
            if age is None or not (0.5 <= age <= 3.0):
                continue
            kind, key, params = classify(st)
            pe = st.pre_early
            i = st.info
            rec["window"].append({
                "age": round(age, 2), "kind": kind, "key": key, "params": params,
                "dex": st.market.dex_id if st.market else None, "complete": i.complete,
                "real": i.real_sol_reserves, "virt": i.virtual_sol_reserves, "quote_mint": i.quote_mint,
                "pump_age_s": round(now - i.pump_updated_at) if i.pump_updated_at else None,
                "bonding_curve": i.bonding_curve, "pair": st.market.pair_address if st.market else None,
                "dq": st.dq_status, "critical": [x.key for x in st.quality.issues if x.severity == "critical"] if st.quality else [],
                "issues": [[x.key, x.severity] for x in st.quality.issues] if st.quality else [], "mayhem": i.mayhem_state,
                "pre": pe.status if pe else None, "blocked_by": list(pe.blocked_by) if pe else []})
    eng.stop()
    await asyncio.gather(task, return_exceptions=True)
    return seen


def report(seen: dict) -> dict:
    in_win = {m: r for m, r in seen.items() if r["window"]}
    last = {m: r["window"][-1] for m, r in in_win.items()}
    kinds = collections.Counter(x["kind"] for x in last.values())
    rejected = {m: x for m, x in last.items() if x["pre"] == "BLOCKED"}
    rej_by_curve = {m: x for m, x in rejected.items() if set(x["critical"]) & CURVE_KEYS}
    rej_other = collections.Counter(b for x in rejected.values() if not (set(x["critical"]) & CURVE_KEYS) for b in x["blocked_by"])
    reasons = collections.Counter()
    samples: dict[str, list] = collections.defaultdict(list)
    for m, x in last.items():
        if x["key"]:
            p = x["params"]
            detail = x["key"]
            if x["key"] == "curve_inconsistent" and p.get("virtual") is not None and p.get("real") is not None:
                detail += f" (virtual−real = {p['virtual'] - p['real']:.2f} SOL)"
            elif x["key"] == "curve_too_small":
                detail += f" (real reserve ${p.get('value')})"
            elif x["key"] == "curve_unavailable":
                detail += f" (sources {','.join(in_win[m]['sources'])}, quote {x['quote_mint'][:6] if x['quote_mint'] else '—'})"
            reasons[detail] += 1
            if len(samples[detail]) < 3:
                samples[detail].append(m)
    pre = collections.Counter(x["pre"] for x in last.values())
    return {"discovered": len(seen), "in_pre_early_window": len(in_win), "last_state_in_window": dict(kinds),
            "pre_early_status": dict(pre), "rejected_total": len(rejected), "rejected_by_curve": len(rej_by_curve),
            "rejected_other_reasons": dict(rej_other.most_common(8)),
            "top_reasons": [{"reason": r, "count": n, "samples": samples[r]} for r, n in reasons.most_common(10)]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=20)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    seen = asyncio.run(run(a.minutes))
    rep = report(seen)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps({"report": rep, "tokens": seen}, ensure_ascii=False), encoding="utf-8")
