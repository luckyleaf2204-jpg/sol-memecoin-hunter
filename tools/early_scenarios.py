"""Print the exact Early-Signal contribution for each controlled scenario (tests/scenarios.py).

  python tools/early_scenarios.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tests"), os.path.join(ROOT, "src")]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scenarios import SCENARIOS, contribution_table, run  # noqa: E402

for sc in SCENARIOS:
    st = run(sc)
    e, r = st.early, st.risk
    denom = sum(x.weight for x in e.signals if x.fired is not None)
    print(f"\n=== {sc.name}: {sc.description}")
    print(f"EARLY SIGNAL = {e.strength}  is_early={e.is_early}  fired={e.fired_count}  transition={e.transition}  "
          f"denominator weight={denom}  lifecycle={st.lifecycle}")
    print(f"SUPPRESSED (D4): {'; '.join(e.suppressed) or 'none'}")
    print(f"RISK = {r.score} {r.level}  flags: " + ", ".join(f"{f.key}+{f.points}" for f in r.factors))
    print(f"Opportunity = {st.score.total if st.score else None}  DQ = {st.quality.status} {st.quality.score}")
    print(f"  {'signal':18s} {'status':10s} {'weight':>6s} {'strength':>8s} {'points':>7s}  value / reason")
    for key, status, w, strength, pts, info in contribution_table(st):
        s = "—" if strength is None else f"{strength:.3f}"
        print(f"  {key:18s} {status:10s} {w:>6} {s:>8s} {pts:>7.1f}  {info}")
    for n in sc.notes:
        print(f"  NOTE: {n}")
