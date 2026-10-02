"""Sample report from the server's data files (paper_bot.json + sample_epoch.json), read-only.

usage: python tools/sample_report.py --book data/paper_bot.json --epoch data/sample_epoch.json [--out report.json]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading.sample_epoch import SampleEpoch  # noqa: E402
from trading.sample_report import report  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--epoch", required=True)
    ap.add_argument("--starting", type=float, default=1000.0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    journal = json.loads(Path(a.book).read_text(encoding="utf-8")).get("journal", [])
    res = report(journal, SampleEpoch.load(Path(a.epoch)), starting=a.starting)
    text = json.dumps(res, indent=1)
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
