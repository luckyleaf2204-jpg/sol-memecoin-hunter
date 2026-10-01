"""Live blocking report: runs the real scanner + PAPER bot for N minutes and prints, for every token the bot
classified, TOKEN / OPP / MOM / CONF / EARLY / IDENTITY / VET / RISK / LIQUIDITY / HOLDER / DEV / BLOCKED_BY,
then the totals (REJECT by gate, UNKNOWN/PENDING, Trade Candidates) and the gate that blocks the most tokens.

usage: python tools/blocking_report.py --minutes 10 [--out report.json]   (HELIUS_API_KEY from the environment)
Read-only diagnostics: it changes no decision, threshold or strategy.
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
from trading.bot import PaperBot  # noqa: E402
from trading.config import TradingConfig  # noqa: E402
from trading.jupiter import JupiterQuotes  # noqa: E402
from trading.serialize import bot_status  # noqa: E402


async def run(minutes: float, research_db: str = "", experimental: bool = True):
    eng = ScannerEngine(Settings(), Database(Path(tempfile.mkdtemp()) / "b.db"), keys=ApiKeys.from_env(),
                        on_log=lambda m: print(m, flush=True) if ("PIPELINE" in m or "FEEDS" in m) else None)
    bot = PaperBot(eng, TradingConfig(experimental=experimental))
    bot.jupiter = JupiterQuotes(eng.http)
    if research_db:
        from research.dataset import DatasetRecorder
        bot.recorder = DatasetRecorder(research_db, dex=eng.dex)
    stop = asyncio.Event()
    tasks = [asyncio.create_task(eng.run()), asyncio.create_task(bot.run(stop))]
    ever_cand, ever_seen = set(), set()
    end = time.time() + minutes * 60
    while time.time() < end:
        await asyncio.sleep(10)
        ever_cand |= {st.mint for st, _, _ in bot.trade_candidates()}
        ever_seen |= set(bot.decisions)
    d = bot_status(bot, eng)
    stop.set()
    eng.stop()
    await asyncio.gather(*tasks, return_exceptions=True)
    return d, len(ever_seen), len(ever_cand), bot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--out", default="")
    ap.add_argument("--old-engine", action="store_true", help="run the OLD decision engine (experimental is default)")
    ap.add_argument("--research-db", default="", help="also record the research dataset (spec Part 1) here")
    a = ap.parse_args()
    d, seen, cand_ever, bot = asyncio.run(run(a.minutes, a.research_db, not a.old_engine))
    ev = sorted(d["evaluated"], key=lambda x: (x["action"] != "BUY", -(x["opportunity"] or -1)))
    print(f"\n{'TOKEN':<12}{'OPP':>5}{'MOM':>5}{'CONF':>6} {'EARLY':<11}{'IDENTITY':<11}{'VET':<7}{'RISK':>5}{'LIQ':>9}"
          f"{'HOLD':>6} {'DEV':<11}ACTION   BLOCKED_BY")
    for x in ev:
        liq = f"${x['liquidity'] / 1e3:.0f}K" if x["liquidity"] else "—"
        print(f"{(x['symbol'] or '')[:11]:<12}{str(x['opportunity'] or '—'):>5}{str(x['momentum'] or '—'):>5}"
              f"{str(x['confidence'] or '—'):>6} {x['early']:<11}{x['identity']:<11}{x['vet']:<7}{str(x['risk'] or '—'):>5}"
              f"{liq:>9}{str(x['holders'] or '—'):>6} {(x['dev'] or '—')[:10]:<11}{x['action']:<8} {', '.join(x['blocked_by'][:4])}")
    s = d["pipeline"]["summary"]
    p = d["pipeline"]
    print("\nTOTALS (current tick)")
    for k, v in s["reject_by"].items():
        print(f"  REJECT bởi {k:<12} {v}")
    print(f"  UNKNOWN/PENDING        {s['unknown_pending']}\n  TRADE CANDIDATE        {s['trade_candidates']}")
    print(f"  first blocker counts   {s['first_blocker']}")
    print(f"  blocked_by (any)       {s['blocked_by_any']}")
    print(f"  MOST BLOCKING GATE     {s['most_blocking']}")
    print(f"\nPIPELINE: discovery {p.get('discovery_per_min')}/min · pre-early {p.get('pre_early_per_min')}/min · "
          f"early-watch {p.get('early_watch_per_min')}/min · WATCH {p['WATCH']} · PENDING-ID {p['PENDING_IDENTITY']} · "
          f"TRADE CANDIDATE {p['TRADE_CANDIDATE']} · REJECT {p['REJECT']} · reasons {p['reject_reasons']}")
    st = d["stats"]
    print(f"over the run: tokens classified {seen} · ever a Trade Candidate {cand_ever} · paper trades closed {st['closed']} "
          f"open {st['open']} · NET P&L {st['net_pnl']}")
    hc = (d.get("helius") or {}).get("credits") or {}
    print(f"helius: {hc.get('used')} credits today · {hc.get('per_hour')}/h · projected month {hc.get('projected_month')}")
    au = bot.audit.report(top=50)
    s_ = au["stats"]
    print(f"\n==== RUN AUDIT ({s_['window_h']} h) ====")
    for k in ("discovery", "pre_early", "early_watch", "early_true", "early_unknown", "early_false", "early_false_partial",
              "watch", "pending", "reject", "early_score_pass", "early_score_pass_by_age", "old_candidates",
              "new_candidates", "trade_candidate", "buy_candidate", "quote_ok", "buy_executed", "buy_simulated_noquote",
              "buy_skipped"):
        print(f"  {k:<22}{s_[k]}")
    print(f"  skips                 {s_['skips']}")
    print(f"  jupiter quote stats   {bot.quote_stats}")
    print("BLOCKED_BY (distinct tokens, any time):")
    for k, v in au["blocked_by"].items():
        print(f"  {k:<22}{v}")
    print("NEAR BUY:", json.dumps(au["near"], ensure_ascii=False))
    print(f"\n{'TOKEN':<12}{'AGE':>6}{'OPP':>5}{'MOM':>5}{'CONF':>6} {'EARLY':<11}{'VET':<26}{'RISK':>5}{'HOLD':>6}{'LIQ':>9}  BLOCKED_BY")
    for x in au["top"]:
        liq = f"${x['liquidity'] / 1e3:.0f}K" if x["liquidity"] else "—"
        print(f"{(x['symbol'] or '')[:11]:<12}{x['age_min']:>6}{str(x['opp']):>5}{str(x['mom'] if x['mom'] is not None else '—'):>5}"
              f"{str(x['conf']):>6} {x['early']:<11}{x['vet'][:25]:<26}{str(x['risk'] if x['risk'] is not None else '—'):>5}"
              f"{str(x['holders'] or '—'):>6}{liq:>9}  {', '.join(x['blocked_by'][:5])}")
    print("\nEXECUTION EVENTS:")
    for e in au["events"][:30]:
        print(f"  {time.strftime('%H:%M:%S', time.localtime(e['ts']))} {e['kind']:<9}{(e['symbol'] or '')[:12]:<13}{e['detail'][:90]} {e['reason']}")
    acts = [x for x in bot.activity if x.text.startswith(("BUY CANDIDATE", "BUY → QUOTE")) or x.kind in ("BUY", "FAILED")]
    print("\nACTIVITY (Candidate -> Quote -> BUY):")
    for x in acts[-30:]:
        print(f"  {time.strftime('%H:%M:%S', time.localtime(x.ts))} {x.kind:<7}{(x.symbol or '')[:12]:<13}{x.text[:140]}")
    if bot.recorder is not None:
        print("\nRESEARCH DATASET:", json.dumps(bot.recorder.summary()))
        bot.recorder.close()
    if a.out:
        d["audit_full"] = au
        Path(a.out).write_text(json.dumps(d, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
