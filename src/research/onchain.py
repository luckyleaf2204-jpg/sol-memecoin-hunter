"""INDEPENDENT anti-rug research data from the chain (Helius RPC: getSignaturesForAddress + getTransaction jsonParsed).
SHADOW ONLY: nothing here touches a BUY, the Risk Engine, exits or any gate.

Per researched token (one fetch, cached by CA):
  * every signature of the mint (slot, blockTime, err)       -> slot structure of the launch (bundle proxy: how many
    transactions landed in the creation slot / first slots)  — measured, not inferred
  * the earliest EARLY_TXS transactions, parsed              -> who bought / sold how much in which slot
    (side from the signer's token + SOL balance change: buy, sell, or token transfer)
  * the creator's latest signatures + post-launch txs        -> dev buys / sells / token transfers / SOL sent to
    other wallets; creator wallet age when its history is short enough to be complete
  * creator funding source                                    -> from the dev analyzer (first incoming SOL), never guessed
Not collectable here (stay UNKNOWN): LP add/remove on AMM pools (program-specific decoding), funding sources of
other buyers (one history fetch per buyer), multi-hop funding chains.

Budget: at most `per_min` tokens per minute (~20 RPC credits each), only while today's Helius spend is on pace;
priorities: open positions, candidates, EarlyScore-PASS tokens, plus a 1/min random sample of other tokens (so
rug rates can be compared inside and outside the momentum filter). Features are rebuilt for any anchor time from
the stored raw summary with only on-chain data up to that time (no look-ahead); fetch latency is recorded.
"""
from __future__ import annotations

import random
import time
from collections import Counter, deque

EARLY_TXS = 12
CREATOR_SIGS = 100
CREATOR_TXS = 6
SOURCE = "Helius RPC getSignaturesForAddress / getTransaction (jsonParsed)"


def _keys(tx):
    try:
        return [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    except (KeyError, TypeError):
        return []


def parse_tx(tx: dict, mint: str, wallet: str | None = None) -> dict | None:
    """Signer (or `wallet`) view of one transaction: token delta of `mint`, SOL delta, side, SOL sent to others."""
    if not tx or not tx.get("meta") or tx["meta"].get("err"):
        return None
    keys = _keys(tx)
    if not keys:
        return None
    who = wallet or keys[0]
    meta = tx["meta"]

    def tok(bals):
        out = {}
        for b in bals or []:
            if b.get("mint") == mint and b.get("owner"):
                try:
                    out[b["owner"]] = out.get(b["owner"], 0.0) + float(b["uiTokenAmount"]["uiAmount"] or 0)
                except (KeyError, TypeError, ValueError):
                    pass
        return out
    pre, post = tok(meta.get("preTokenBalances")), tok(meta.get("postTokenBalances"))
    deltas = {o: post.get(o, 0.0) - pre.get(o, 0.0) for o in set(pre) | set(post)}
    d_tok = deltas.get(who, 0.0)
    try:
        i = keys.index(who)
        d_sol = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
    except (ValueError, KeyError, IndexError):
        d_sol = None
    side = None
    if d_tok > 0:
        side = "buy"
    elif d_tok < 0:
        side = "sell" if d_sol is not None and d_sol > 0.001 else "transfer"
    sol_out = []
    for ins in (tx["transaction"]["message"].get("instructions") or []):
        p = ins.get("parsed") if isinstance(ins, dict) else None
        if isinstance(p, dict) and p.get("type") == "transfer" and ins.get("program") == "system":
            info = p.get("info") or {}
            if info.get("source") == who and info.get("destination"):
                sol_out.append((info["destination"], (info.get("lamports") or 0) / 1e9))
    return {"slot": tx.get("slot"), "t": tx.get("blockTime"), "who": who, "side": side, "tokens": round(d_tok, 4),
            "sol": None if d_sol is None else round(d_sol, 6), "sol_out": sol_out,
            "deltas": {o: round(v, 4) for o, v in deltas.items() if v},
            "pre_bal": {o: round(v, 4) for o, v in pre.items() if v}}


def pool_owners(txs: list[dict]) -> set[str]:
    """Owners whose token balance moves in most of the early transactions = the bonding curve / pool account(s).
    Measured from the transactions themselves (the counterparty of every trade), not assumed."""
    c = Counter(o for x in txs for o in (x.get("deltas") or {}))
    n = len([x for x in txs if x.get("deltas")])
    return {o for o, k in c.items() if n >= 3 and k >= 0.5 * n}


def trades(x: dict, pools: set[str]) -> tuple[dict, dict]:
    """(buyers, sellers) of one parsed transaction: non-pool owners whose balance went up / down."""
    d = x.get("deltas") or {}
    return ({o: v for o, v in d.items() if v > 0 and o not in pools},
            {o: -v for o, v in d.items() if v < 0 and o not in pools})


class OnchainResearch:
    def __init__(self, rpc, recorder=None, per_min: int = 3, sample_per_min: int = 1, seed: int = 5):
        self.rpc, self.recorder = rpc, recorder
        self.per_min, self.sample_per_min = per_min, sample_per_min
        self.done: dict[str, dict] = {}
        self.calls: deque = deque()
        self.samples: deque = deque()
        self.rng = random.Random(seed)
        self.n = {"tokens": 0, "rpc_calls": 0, "failures": 0, "skipped_budget": 0, "skipped_rate": 0}

    # ---------------------------------------------------------------- budget
    def budget_ok(self, now: float) -> bool:
        try:
            st = self.rpc.credits.state()
            allowed = (st.get("daily_budget") or 0) * min(1.0, (now % 86400) / 86400 + 0.05)
            if st.get("quota_exhausted") or (allowed and st.get("used", 0) > 0.8 * allowed):
                return False
            return self.rpc.credits.allow(30)
        except (AttributeError, TypeError):
            return bool(getattr(self.rpc, "has_das", False))

    def _rate_ok(self, now: float) -> bool:
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        return len(self.calls) < self.per_min

    # ---------------------------------------------------------------- selection
    def select(self, states: dict, decisions: dict, positions: set, now: float) -> list:
        pri = []
        for mint, st in states.items():
            if mint in self.done:
                continue
            rec = decisions.get(mint) or {}
            es = rec.get("early_score") or {}
            es_pass = es.get("score") is not None and es["score"] >= es.get("theta", 1) and es["confidence"] >= es.get("gamma", 1)
            p = 0 if mint in positions else 1 if rec.get("decision") == "TRADE" else 2 if es_pass else None
            if p is not None:
                pri.append((p, -(rec.get("opportunity") or 0), mint))
        pri.sort()
        out = [states[m] for _, _, m in pri]
        chosen = {m for _, _, m in pri}
        while self.samples and now - self.samples[0] >= 60:
            self.samples.popleft()
        if len(self.samples) < self.sample_per_min:
            pool = [st for m, st in states.items() if m not in self.done and m not in chosen and st.info.created_at
                    and now - st.info.created_at >= 60]
            if pool:
                pick = self.rng.choice(pool)
                self.samples.append(now)
                pick._research_sample = True          # noqa: SLF001 (marker for the record only)
                out.append(pick)
        return out

    async def round(self, states: dict, decisions: dict, positions: set, now: float | None = None) -> int:
        now = now or time.time()
        made = 0
        for st in self.select(states, decisions, positions, now):
            if not self._rate_ok(now):
                self.n["skipped_rate"] += 1
                break
            if not self.budget_ok(now):
                self.n["skipped_budget"] += 1
                break
            self.calls.append(now)
            rec = await self.collect(st, decisions.get(st.mint), now)
            self.done[st.mint] = rec
            made += 1
            if self.recorder is not None:
                try:
                    self.recorder.onchain(rec)
                except Exception:
                    pass
        if len(self.done) > 20000:
            for m in list(self.done)[:5000]:
                self.done.pop(m, None)
        return made

    # ---------------------------------------------------------------- collection
    async def _call(self, coro):
        self.n["rpc_calls"] += 1
        try:
            return await coro
        except Exception:
            self.n["failures"] += 1
            return None

    async def collect(self, st, rec: dict | None, now: float) -> dict:
        t0 = time.time()
        mint, creator = st.mint, st.info.creator
        out = {"ca": mint, "fetched_ts": now, "source": SOURCE, "creator": creator or None,
               "sampled": bool(getattr(st, "_research_sample", False)),
               "selected_as": "position/candidate/earlyscore" if not getattr(st, "_research_sample", False) else "random",
               "decision_at_fetch": (rec or {}).get("decision"), "status": "ok"}
        sigs = await self._call(self.rpc.signatures(mint, limit=1000))
        if sigs is None:
            out["status"] = "UNKNOWN (signatures unavailable)"
            out["latency_s"] = round(time.time() - t0, 2)
            self.n["failures"] += 1
            return out
        okc = [s for s in sigs if not s.get("err") and s.get("slot") is not None]
        out["mint_sigs_complete"] = len(sigs) < 1000
        out["mint_sigs"] = sorted([(s["slot"], s.get("blockTime")) for s in okc])[:400]
        earliest = sorted(okc, key=lambda s: (s["slot"], s.get("blockTime") or 0))[:EARLY_TXS]
        early = []
        for s in earliest:
            tx = await self._call(self.rpc.transaction(s["signature"]))
            p = parse_tx(tx, mint) if tx else None
            if p:
                early.append(p)
        out["early_txs"] = early
        if creator:
            csigs = await self._call(self.rpc.signatures(creator, limit=CREATOR_SIGS))
            if csigs is not None:
                cok = [s for s in csigs if not s.get("err")]
                out["creator_sigs_complete"] = len(csigs) < CREATOR_SIGS
                times = [s.get("blockTime") for s in cok if s.get("blockTime")]
                out["creator_first_seen"] = min(times) if times else None    # exact if complete, else lower bound
                launch = out["mint_sigs"][0][1] if out["mint_sigs"] else None
                post = [s for s in cok if launch is None or (s.get("blockTime") or 0) >= launch]
                flows = []
                for s in sorted(post, key=lambda s: s.get("blockTime") or 0)[:CREATOR_TXS]:
                    tx = await self._call(self.rpc.transaction(s["signature"]))
                    p = parse_tx(tx, mint, wallet=creator) if tx else None
                    if p:
                        flows.append(p)
                out["creator_flows"] = flows
            else:
                out["creator_flows"] = None
        d = st.dev
        out["funding_wallet"] = d.funding_wallet if d else None
        out["funding_sol"] = d.funding_sol if d else None
        out["funding_note"] = d.funding_note if d else "dev analysis not run"
        out["total_supply"] = st.info.total_supply
        out["latency_s"] = round(time.time() - t0, 2)
        self.n["tokens"] += 1
        return out

    def stats(self) -> dict:
        return dict(self.n) | {"researched": len(self.done), "per_min_limit": self.per_min}


# ---------------------------------------------------------------- point-in-time features (no look-ahead)
def features_at(r: dict | None, t: float) -> dict:
    """On-chain features of a token AS OF time t, from the stored summary: only transactions with blockTime <= t.
    Every value is None (UNKNOWN) when the data was not collected or not complete enough."""
    f = {k: None for k in ("create_slot_txs", "first3slots_txs", "max_txs_per_slot", "sync_buy_slots",
                           "sync_sell_slots", "early_buyers", "early_buyer_supply_pct", "creator_funded_buyers",
                           "funder_bought", "dev_buys", "dev_sells", "dev_token_transfers", "dev_sol_out_wallets",
                           "creator_wallet_age_s", "creator_age_is_lower_bound", "lp_events", "buyer_shared_funding")}
    f["onchain_status"] = "UNKNOWN (not researched)" if not r else r.get("status")
    if not r or r.get("status") != "ok":
        return f
    sigs = [(s, bt) for s, bt in r.get("mint_sigs") or [] if bt is None or bt <= t]
    if sigs and r.get("mint_sigs_complete"):
        slots = Counter(s for s, _ in sigs)
        s0 = min(slots)
        f["create_slot_txs"] = slots[s0]
        f["first3slots_txs"] = sum(c for s, c in slots.items() if s <= s0 + 2)
        f["max_txs_per_slot"] = max(c for s, c in slots.items() if s <= s0 + 20)
    early = [x for x in r.get("early_txs") or [] if x.get("t") is None or x["t"] <= t]
    if early:
        pools = pool_owners(r.get("early_txs") or [])
        by_slot: dict = {}
        buyers: dict = {}
        for x in early:
            b, sl = trades(x, pools)
            e = by_slot.setdefault(x["slot"], [set(), set()])
            e[0] |= set(b)
            e[1] |= set(sl)
            for o, v in b.items():
                if o != r.get("creator"):
                    buyers[o] = buyers.get(o, 0.0) + v
        f["sync_buy_slots"] = sum(1 for b, _ in by_slot.values() if len(b) >= 3)
        f["sync_sell_slots"] = sum(1 for _, sl in by_slot.values() if len(sl) >= 2)
        f["early_buyers"] = len(buyers)
        sup = r.get("total_supply")
        f["early_buyer_supply_pct"] = round(100 * sum(buyers.values()) / sup, 2) if sup else None
        fw = r.get("funding_wallet")
        f["funder_bought"] = (fw in buyers) if fw else None
        flows = r.get("creator_flows")
        if flows is not None:
            fl = [x for x in flows if x.get("t") is None or x["t"] <= t]
            f["dev_buys"] = sum(1 for x in fl if x["side"] == "buy")
            f["dev_sells"] = sum(1 for x in fl if x["side"] == "sell")
            f["dev_token_transfers"] = sum(1 for x in fl if x["side"] == "transfer")
            sent = {d for x in fl for d, _ in x.get("sol_out") or []}
            f["dev_sol_out_wallets"] = len(sent)
            f["creator_funded_buyers"] = len(sent & set(buyers))
    if r.get("creator_first_seen"):
        f["creator_wallet_age_s"] = max(0, t - r["creator_first_seen"])
        f["creator_age_is_lower_bound"] = not r.get("creator_sigs_complete")
    return f


def shadow_v2(f: dict) -> tuple[int | None, list[str]]:
    """SHADOW ANTI_RUG_SCORE_V2 (pre-registered, on-chain only, independent of momentum). None if no on-chain data."""
    if f.get("onchain_status") != "ok":
        return None, ["no on-chain data"]
    pts, why = 0, []

    def add(p, r):
        nonlocal pts
        pts += p
        why.append(f"{r} +{p}")
    if (f.get("create_slot_txs") or 0) >= 3:
        add(20, "3+ txs in the creation slot (bundle proxy)")
    if (f.get("sync_buy_slots") or 0) >= 1:
        add(15, "3+ wallets bought in one slot")
    if (f.get("early_buyer_supply_pct") or 0) >= 30:
        add(15, "first buyers hold >= 30% supply")
    if (f.get("creator_funded_buyers") or 0) >= 1:
        add(25, "early buyer funded by the creator")
    if f.get("funder_bought"):
        add(20, "creator's funder bought early")
    age = f.get("creator_wallet_age_s")
    if age is not None and not f.get("creator_age_is_lower_bound"):
        if age < 3600:
            add(15, "creator wallet < 1 h old")
        elif age < 86400:
            add(10, "creator wallet < 1 day old")
    if (f.get("dev_sells") or 0) >= 1:
        add(20, "dev sold")
    if (f.get("dev_token_transfers") or 0) >= 1:
        add(15, "dev moved tokens to another wallet")
    if (f.get("sync_sell_slots") or 0) >= 1:
        add(10, "2+ wallets sold in one slot")
    return min(100, pts), why
