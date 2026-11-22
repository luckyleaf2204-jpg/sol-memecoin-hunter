"""Token risk checks (PHẦN X). The result is a relative classification under this system's
rules — it NEVER means a token is safe.

Points: each check adds points; unknown data adds 0.5 (missing data is not "safe").
  HIGH   : >= 5 points or any critical check
  MEDIUM : 2 – 4.5 points
  LOW    : < 2 points
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DISCLAIMER = ("Phân loại rủi ro tương đối theo bộ tiêu chí của hệ thống, không phải khẳng định token an toàn. "
              "Relative classification only — never a statement that a token is safe.")


@dataclass
class RiskInputs:
    dev_holding_pct: float | None = None          # % of supply held by the creator now
    top10_pct: float | None = None                # % of supply held by top 10 (curve/pool excluded)
    holders_source: str | None = None
    early_buyers_common_funder_share: float | None = None
    bundle_wallets: int | None = None             # distinct non-dev wallets buying in the creation slot
    bundle_supply_pct: float | None = None
    liquidity_sol: float | None = None
    volume_5m_sol: float | None = None
    wash_volume_share: float | None = None
    dev_sold_share: float | None = None           # share of dev's bought tokens already sold
    dev_sells: int | None = None
    mint_authority: str | None = "unknown"        # None = revoked
    freeze_authority: str | None = "unknown"
    on_curve: bool | None = None
    sell_route: bool | None = None                # Jupiter quote available
    sell_price_impact: float | None = None
    # phase 2 — holder structure and flow quality
    max_holder_pct: float | None = None           # largest single wallet (curve/pool excluded)
    max_holder_wallet: str | None = None
    holder_alert_pct: float = 5.0
    linked_holders_pct: float | None = None       # % of supply held by top holders sharing a funder
    linked_groups: int | None = None
    fresh_top_holders: int | None = None          # top-10 holders whose first activity is recent
    fresh_hours: float = 48
    unnatural_volume: bool | None = None
    unnatural_reasons: list | None = None
    fees_sol: float | None = None
    volume_to_mc: float | None = None


@dataclass
class RiskResult:
    level: str
    points: float
    checks: list[dict[str, Any]] = field(default_factory=list)
    disclaimer: str = DISCLAIMER


def _chk(out: list, key: str, status: str, points: float, detail: str, critical: bool = False) -> None:
    out.append({"check": key, "status": status, "points": points, "detail": detail, "critical": critical})


def assess(i: RiskInputs) -> RiskResult:
    c: list[dict[str, Any]] = []

    if i.dev_holding_pct is None:
        _chk(c, "dev_holding", "unknown", 0.5, "creator balance not available")
    elif i.dev_holding_pct > 10:
        _chk(c, "dev_holding", "fail", 3, f"creator holds {i.dev_holding_pct:.1f}% of supply", True)
    elif i.dev_holding_pct > 5:
        _chk(c, "dev_holding", "warn", 1, f"creator holds {i.dev_holding_pct:.1f}%")
    else:
        _chk(c, "dev_holding", "ok", 0, f"creator holds {i.dev_holding_pct:.1f}%")

    if i.top10_pct is None:
        _chk(c, "top10_concentration", "unknown", 0.5, "holder distribution not available")
    elif i.top10_pct > 50:
        _chk(c, "top10_concentration", "fail", 3, f"top 10 holders own {i.top10_pct:.1f}% ({i.holders_source})", True)
    elif i.top10_pct > 30:
        _chk(c, "top10_concentration", "warn", 1, f"top 10 holders own {i.top10_pct:.1f}% ({i.holders_source})")
    else:
        _chk(c, "top10_concentration", "ok", 0, f"top 10 holders own {i.top10_pct:.1f}% ({i.holders_source})")

    if i.early_buyers_common_funder_share is None:
        _chk(c, "common_funding", "unknown", 0.5, "funding of early buyers not analysed")
    elif i.early_buyers_common_funder_share >= 0.3:
        _chk(c, "common_funding", "fail", 2, f"{i.early_buyers_common_funder_share:.0%} of early buyers share a funder")
    else:
        _chk(c, "common_funding", "ok", 0, f"{i.early_buyers_common_funder_share:.0%} of early buyers share a funder")

    if i.bundle_wallets is None:
        _chk(c, "bundle_buying", "unknown", 0.5, "creation slot not analysed")
    elif i.bundle_wallets >= 3 or (i.bundle_supply_pct or 0) >= 15:
        _chk(c, "bundle_buying", "fail", 3, f"{i.bundle_wallets} wallet(s) bought in the creation slot "
                                             f"({(i.bundle_supply_pct or 0):.1f}% supply)", True)
    elif i.bundle_wallets >= 1:
        _chk(c, "bundle_buying", "warn", 1, f"{i.bundle_wallets} wallet(s) bought in the creation slot")
    else:
        _chk(c, "bundle_buying", "ok", 0, "no other wallet bought in the creation slot")

    if i.liquidity_sol is None:
        _chk(c, "liquidity", "unknown", 1, "liquidity unknown")
    elif i.liquidity_sol < 5:
        _chk(c, "liquidity", "fail", 2, f"only {i.liquidity_sol:.2f} SOL liquidity")
    elif i.liquidity_sol < 20:
        _chk(c, "liquidity", "warn", 1, f"{i.liquidity_sol:.1f} SOL liquidity")
    else:
        _chk(c, "liquidity", "ok", 0, f"{i.liquidity_sol:.1f} SOL liquidity")

    if i.volume_5m_sol is not None and i.liquidity_sol:
        r = i.volume_5m_sol / i.liquidity_sol
        if r > 10:
            _chk(c, "volume_anomaly", "warn", 1, f"5m volume = {r:.1f}x liquidity")
        else:
            _chk(c, "volume_anomaly", "ok", 0, f"5m volume = {r:.1f}x liquidity")
    else:
        _chk(c, "volume_anomaly", "unknown", 0.5, "volume or liquidity unknown")

    if i.wash_volume_share is None:
        _chk(c, "wash_trading", "unknown", 0.5, "not enough trades to analyse")
    elif i.wash_volume_share >= 0.3:
        _chk(c, "wash_trading", "fail", 2, f"{i.wash_volume_share:.0%} of volume from fast round trips")
    else:
        _chk(c, "wash_trading", "ok", 0, f"{i.wash_volume_share:.0%} of volume from fast round trips")

    if i.dev_sold_share is None and i.dev_sells is None:
        _chk(c, "dev_selling", "unknown", 0.5, "creator trades not observed")
    elif (i.dev_sold_share or 0) >= 0.5:
        _chk(c, "dev_selling", "fail", 3, f"creator already sold {i.dev_sold_share:.0%} of his buy", True)
    elif (i.dev_sells or 0) > 0:
        _chk(c, "dev_selling", "warn", 1, f"creator sold {(i.dev_sold_share or 0):.0%}")
    else:
        _chk(c, "dev_selling", "ok", 0, "no creator sells observed")

    if i.mint_authority == "unknown":
        _chk(c, "mint_authority", "unknown", 0.5, "mint account not read")
    elif i.mint_authority:
        _chk(c, "mint_authority", "fail", 3, f"mint authority still set ({i.mint_authority[:8]}…)", True)
    else:
        _chk(c, "mint_authority", "ok", 0, "mint authority revoked")
    if i.freeze_authority == "unknown":
        _chk(c, "freeze_authority", "unknown", 0.5, "mint account not read")
    elif i.freeze_authority:
        _chk(c, "freeze_authority", "fail", 3, f"freeze authority set ({i.freeze_authority[:8]}…)", True)
    else:
        _chk(c, "freeze_authority", "ok", 0, "freeze authority revoked")

    if i.on_curve:
        _chk(c, "sellable", "ok", 0, "bonding curve active: sells go to the curve")
    elif i.sell_route is None:
        _chk(c, "sellable", "unknown", 1, "no sell quote obtained")
    elif not i.sell_route:
        _chk(c, "sellable", "fail", 3, "no route to sell the token for SOL", True)
    elif (i.sell_price_impact or 0) > 0.10:
        _chk(c, "sellable", "warn", 1, f"sell price impact {i.sell_price_impact:.0%}")
    else:
        _chk(c, "sellable", "ok", 0, "sell route available")

    # ---- phase 2 checks. Missing data here is shown as "unknown" but adds no point: the
    # top-10 check above already charges for unknown holder data.
    if i.max_holder_pct is None:
        _chk(c, "single_holder", "unknown", 0, "holder distribution not available")
    elif i.max_holder_pct > 2 * i.holder_alert_pct:
        _chk(c, "single_holder", "fail", 3, f"one wallet holds {i.max_holder_pct:.1f}% of supply "
                                             f"({(i.max_holder_wallet or '')[:8]}…)", True)
    elif i.max_holder_pct > i.holder_alert_pct:
        _chk(c, "single_holder", "fail", 2, f"one wallet holds {i.max_holder_pct:.1f}% of supply "
                                             f"({(i.max_holder_wallet or '')[:8]}…)")
    else:
        _chk(c, "single_holder", "ok", 0, f"largest holder {i.max_holder_pct:.1f}%")

    if i.linked_holders_pct is None:
        _chk(c, "linked_holders", "unknown", 0, "funders of top holders not known")
    elif i.linked_holders_pct >= 10:
        # one owner behind several wallets holding >= 10 % is as bad as one wallet holding > 10 %
        _chk(c, "linked_holders", "fail", 3, f"{i.linked_groups} group(s) of top holders with a common funder hold "
                                               f"{i.linked_holders_pct:.1f}%", True)
    elif (i.linked_groups or 0) > 0:
        _chk(c, "linked_holders", "warn", 1, f"{i.linked_groups} group(s) of linked top holders "
                                               f"({i.linked_holders_pct:.1f}%)")
    else:
        _chk(c, "linked_holders", "ok", 0, "no top holders sharing a funder")

    if i.fresh_top_holders is None:
        _chk(c, "fresh_wallets", "unknown", 0, "age of top holders not checked")
    elif i.fresh_top_holders >= 3:
        _chk(c, "fresh_wallets", "fail", 2, f"{i.fresh_top_holders} top holders are wallets created < {i.fresh_hours:g} h ago")
    elif i.fresh_top_holders >= 1:
        _chk(c, "fresh_wallets", "warn", 1, f"{i.fresh_top_holders} top holder(s) created < {i.fresh_hours:g} h ago")
    else:
        _chk(c, "fresh_wallets", "ok", 0, "no newly created wallets among top holders")

    if i.unnatural_volume is None:
        _chk(c, "unnatural_volume", "unknown", 0, "flow data not available")
    elif i.unnatural_volume:
        _chk(c, "unnatural_volume", "warn", 1, "; ".join(i.unnatural_reasons or []) + " (heuristic)")
    else:
        _chk(c, "unnatural_volume", "ok", 0, "; ".join(i.unnatural_reasons or []) or "no bot-volume pattern")

    if i.fees_sol is not None:
        _chk(c, "fees_generated", "info", 0, f"{i.fees_sol:.2f} SOL trading fees generated"
             + (f", volume = {i.volume_to_mc:.1f}x market cap" if i.volume_to_mc is not None else "")
             + " (reference only, not scored)")

    pts = sum(x["points"] for x in c)
    critical = any(x["critical"] and x["status"] == "fail" for x in c)
    level = "HIGH" if (pts >= 5 or critical) else ("MEDIUM" if pts >= 2 else "LOW")
    return RiskResult(level=level, points=pts, checks=c)


def wash_share(trades: list[tuple[str, int, str, float]], window: int = 60, tol: float = 0.15) -> float | None:
    """trades: (wallet, ts, side, sol). Volume share of wallets that buy and sell roughly the same
    SOL amount within `window` seconds (round trips)."""
    if len(trades) < 10:
        return None
    total = sum(t[3] for t in trades) or 0
    if total <= 0:
        return None
    by_w: dict[str, list] = {}
    for t in sorted(trades, key=lambda x: x[1]):
        by_w.setdefault(t[0], []).append(t)
    wash = 0.0
    for lst in by_w.values():
        buys = [t for t in lst if t[2] == "buy"]
        sells = [t for t in lst if t[2] == "sell"]
        used = set()
        for b in buys:
            for k, s in enumerate(sells):
                if k in used:
                    continue
                if 0 <= s[1] - b[1] <= window and abs(s[3] - b[3]) <= tol * max(b[3], 1e-9):
                    wash += b[3] + s[3]
                    used.add(k)
                    break
    return min(1.0, wash / total)
