"""Render FREE plan instance hours: 750 h per workspace per month (render.com/docs/free). When they run out, Render
suspends ALL free web services of the workspace until the next month.

This estimates the month if the service runs 24/7 (keep-alive on). The authoritative number is in the Render
Dashboard -> Billing -> "Monthly Included Usage".

usage: python tools/free_hours.py [--services 1] [--date 2026-10-02]
"""
import argparse
import calendar
from datetime import datetime, timezone

FREE_HOURS = 750


def estimate(now: datetime, services: int = 1) -> dict:
    days = calendar.monthrange(now.year, now.month)[1]
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = services * (now - start).total_seconds() / 3600
    month = services * days * 24
    return {"month_hours_if_always_on": month, "used_so_far_if_always_on": round(used, 1),
            "remaining_now": round(FREE_HOURS - used, 1), "spare_at_month_end": FREE_HOURS - month,
            "runs_out": month > FREE_HOURS,
            "runs_out_on_day": None if month <= FREE_HOURS else int(FREE_HOURS / (24 * services)) + 1}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--services", type=int, default=1, help="always-on free web services in the workspace")
    ap.add_argument("--date", default="", help="YYYY-MM-DD (UTC); default now")
    a = ap.parse_args()
    now = datetime.fromisoformat(a.date).replace(tzinfo=timezone.utc) if a.date else datetime.now(timezone.utc)
    e = estimate(now, a.services)
    print(f"{now:%Y-%m}: {a.services} always-on service(s) need {e['month_hours_if_always_on']} h of {FREE_HOURS} h; "
          f"used so far ~{e['used_so_far_if_always_on']} h, remaining ~{e['remaining_now']} h; "
          + (f"RUNS OUT on day {e['runs_out_on_day']}" if e["runs_out"] else f"{e['spare_at_month_end']} h spare"))


if __name__ == "__main__":
    main()
