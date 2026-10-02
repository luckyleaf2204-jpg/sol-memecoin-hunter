"""Export a review bundle (sample report + last 50 trades + gate statistics + durability) to docs/review_bundle.md,
so a reviewer can read the state of the PAPER sample on GitHub without server access. No secret is written.

usage (owner, from the running server; the access code is read from the environment, never from the command line):
  REVIEW_ACCESS_CODE=... python tools/export_review_bundle.py --url https://sol-memecoin-hunter.onrender.com
usage (from copies of the data files):
  python tools/export_review_bundle.py --data data [--research-db data/research.db]
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading.review_bundle import build, check_no_secrets, to_markdown  # noqa: E402


def from_files(data: Path, research_db: Path | None) -> dict:
    from trading.gate_stats import block_rates
    from trading.sample_epoch import SampleEpoch
    from trading.sample_report import report, summary_line
    book_p, ep_p = data / "paper_bot.json", data / "sample_epoch.json"
    book = json.loads(book_p.read_text(encoding="utf-8")) if book_p.exists() else {}
    epoch = SampleEpoch.load(ep_p) if ep_p.exists() else None
    rep = report(book.get("journal", []), epoch, gaps=book.get("gaps", []))
    rep["gate_block_rates"] = block_rates(book.get("gate_seen", {}), epoch.started_at if epoch else None)
    if research_db and research_db.exists():
        from research.gate_eval import evaluate
        rep["gate_forward_returns"] = evaluate(str(research_db), 7, epoch.started_at if epoch else None)
    else:
        rep["gate_forward_returns"] = {"status": "research.db not given"}
    rep["summary_line"] = summary_line(rep)
    files = {"paper_bot.json": book_p.exists(), "sample_epoch.json": ep_p.exists()}
    src = f"local files in {data.name}/" + ("" if any(files.values()) else
                                             " — NONE found: the paper sample lives on the server; the owner runs "
                                             "this tool with --url to export it")
    return build(book.get("journal", []), rep, {"status": "offline bundle from local files", "files": files}, source=src)


def from_server(url: str) -> dict:
    import httpx
    code = os.environ.get("REVIEW_ACCESS_CODE", "")
    if not code:
        sys.exit("set REVIEW_ACCESS_CODE in the environment (never on the command line)")
    r = httpx.get(url.rstrip("/") + "/api/review_bundle", headers={"X-Access-Code": code}, timeout=120)
    r.raise_for_status()
    return r.json()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # Vietnamese text on a Windows console
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--data", default="")
    ap.add_argument("--research-db", default="")
    ap.add_argument("--out", default=str(ROOT / "docs" / "review_bundle.md"))
    ap.add_argument("--json", default="", help="also write the bundle as JSON here")
    a = ap.parse_args()
    if a.url:
        b = from_server(a.url)
    elif a.data:
        b = from_files(Path(a.data), Path(a.research_db) if a.research_db else None)
    else:
        sys.exit("--url or --data")
    md = to_markdown(b)                              # refuses if anything credential-like slipped in
    Path(a.out).write_text(md, encoding="utf-8")
    if a.json:
        text = json.dumps(b, indent=1, default=str)
        check_no_secrets(text)
        Path(a.json).write_text(text, encoding="utf-8")
    print(b.get("summary_line") or "")
    print(f"written: {a.out}")


if __name__ == "__main__":
    main()
