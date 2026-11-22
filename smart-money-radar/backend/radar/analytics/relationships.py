"""Wallet relationship evidence and clustering ("independent" Smart Money).

Edges (wallet_a < wallet_b), each with a strength in [0, 1] and on-chain evidence:
  funded_by             one wallet funded the other with SOL                    0.90
  direct_sol_transfer   SOL moved directly between the two wallets (>= 0.1)     0.85
  direct_token_transfer tokens moved directly between the two wallets           0.90
  common_funder         both first funded by the same wallet, which funds few
                        wallets (a funder with >= `exchange_fanout` children is
                        treated as an exchange / service and ignored)          0.75
  same_creator          both linked (funded by / funding / being) the same token
                        creator                                                0.70
  sync_buys             bought the same tokens within `sync_slots` slots on
                        >= `sync_min_tokens` different tokens                  0.4 + 0.1 per token (max 1)

Clusters = connected components over edges with strength >= min_strength (union-find).
A signal counts clusters, never raw wallets, so 5 wallets of one owner are ONE buyer.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Edge:
    a: str
    b: str
    relation: str
    strength: float
    evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.a > self.b:
            self.a, self.b = self.b, self.a


class UnionFind:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        root = x
        while self.p[root] != root:
            root = self.p[root]
        while self.p[x] != root:
            self.p[x], x = root, self.p[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:
                ra, rb = rb, ra
            self.p[rb] = ra


def funding_edges(funders: dict[str, tuple[str, str | None]], exchange_fanout: int = 20) -> list[Edge]:
    """funders: wallet -> (funder, signature)."""
    edges: list[Edge] = []
    children: dict[str, list[str]] = defaultdict(list)
    for w, (f, _sig) in funders.items():
        if f and f != w:
            children[f].append(w)
    tracked = set(funders)
    for w, (f, sig) in funders.items():
        if f in tracked and f != w:
            edges.append(Edge(w, f, "funded_by", 0.9, {"signature": sig}))
    for f, kids in children.items():
        if len(kids) >= exchange_fanout or len(kids) < 2:
            continue
        kids = sorted(kids)
        for i in range(len(kids)):
            for j in range(i + 1, len(kids)):
                edges.append(Edge(kids[i], kids[j], "common_funder", 0.75,
                                  {"funder": f, "signatures": [funders[kids[i]][1], funders[kids[j]][1]]}))
    return edges


def transfer_edges(transfers: Iterable[tuple[str, str, str, float, str]], min_sol: float = 0.1) -> list[Edge]:
    """transfers: (from, to, kind 'sol'|'token', amount, signature) between tracked wallets."""
    agg: dict[tuple[str, str, str], dict] = {}
    for a, b, kind, amount, sig in transfers:
        if a == b:
            continue
        if kind == "sol" and amount < min_sol:
            continue
        rel = "direct_sol_transfer" if kind == "sol" else "direct_token_transfer"
        key = (min(a, b), max(a, b), rel)
        d = agg.setdefault(key, {"count": 0, "amount": 0.0, "signatures": []})
        d["count"] += 1
        d["amount"] += amount
        if len(d["signatures"]) < 5:
            d["signatures"].append(sig)
    return [Edge(a, b, rel, 0.85 if rel == "direct_sol_transfer" else 0.9, ev) for (a, b, rel), ev in agg.items()]


def sync_buy_edges(buys: dict[str, list[tuple[str, int]]], sync_slots: int = 2, min_tokens: int = 3,
                   min_jaccard: float = 0.25) -> list[Edge]:
    """buys: wallet -> [(mint, slot)] (first buy slot per position)."""
    by_mint: dict[str, list[tuple[str, int]]] = defaultdict(list)
    tokens_of: dict[str, set[str]] = defaultdict(set)
    for w, lst in buys.items():
        for mint, slot in lst:
            by_mint[mint].append((w, slot))
            tokens_of[w].add(mint)
    pair_tokens: dict[tuple[str, str], set[str]] = defaultdict(set)
    for mint, lst in by_mint.items():
        lst.sort(key=lambda x: x[1])
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j][1] - lst[i][1] > sync_slots:
                    break
                a, b = lst[i][0], lst[j][0]
                if a != b:
                    pair_tokens[(min(a, b), max(a, b))].add(mint)
    edges = []
    for (a, b), toks in pair_tokens.items():
        if len(toks) < min_tokens:
            continue
        union = len(tokens_of[a] | tokens_of[b]) or 1
        jac = len(toks) / union
        if jac < min_jaccard:
            continue
        edges.append(Edge(a, b, "sync_buys", min(1.0, 0.4 + 0.1 * len(toks)),
                          {"tokens": sorted(toks)[:20], "count": len(toks), "jaccard": round(jac, 3)}))
    return edges


def creator_edges(creator_links: dict[str, set[str]]) -> list[Edge]:
    """creator_links: wallet -> set of creators it is linked to (is / funded / was funded by)."""
    by_creator: dict[str, list[str]] = defaultdict(list)
    for w, cs in creator_links.items():
        for c in cs:
            by_creator[c].append(w)
    edges = []
    for c, ws in by_creator.items():
        ws = sorted(set(ws))
        if len(ws) > 30:
            continue
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                edges.append(Edge(ws[i], ws[j], "same_creator", 0.7, {"creator": c}))
    return edges


def clusters(wallets: Iterable[str], edges: Iterable[Edge], min_strength: float = 0.6) -> dict[str, str]:
    uf = UnionFind()
    for w in wallets:
        uf.find(w)
    for e in edges:
        if e.strength >= min_strength:
            uf.union(e.a, e.b)
    return {w: uf.find(w) for w in uf.p}


def cluster_sizes(assign: dict[str, str]) -> dict[str, int]:
    size: dict[str, int] = defaultdict(int)
    for c in assign.values():
        size[c] += 1
    return {w: size[c] for w, c in assign.items()}
