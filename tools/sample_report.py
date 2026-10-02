"""Sample report from the server's data files (paper_bot.json + sample_epoch.json), read-only.

usage: python tools/sample_report.py --book data/paper_bot.json --epoch data/sample_epoch.json
         [--research-db data/research.db] [--out report.json]
The first printed line starts with the sample status and the conclusion ("Chưa đủ dữ liệu để kết luận có lãi" until
n >= 200 and every 95 % CI, plain and per token, is on one side of 0).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading.sample_epoch import SampleEpoch  # noqa: E402
from trading.sample_report import report, summary_line  # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # Vietnamese text on a Windows console
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--epoch", required=True)
    ap.add_argument("--starting", type=float, default=1000.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--research-db", default="", help="research.db: blocked vs entered vs baseline forward returns")
    a = ap.parse_args()
    book = json.loads(Path(a.book).read_text(encoding="utf-8"))
    epoch = SampleEpoch.load(Path(a.epoch))
    res = report(book.get("journal", []), epoch, starting=a.starting, gaps=book.get("gaps", []))
    from trading.gate_stats import block_rates
    res["gate_block_rates"] = block_rates(book.get("gate_seen", {}), epoch.started_at)
    if a.research_db:
        from research.gate_eval import evaluate
        res["gate_forward_returns"] = evaluate(a.research_db, 7, epoch.started_at)
    text = json.dumps(res, indent=1)
    print(summary_line(res))                     # n, status, CI first
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
