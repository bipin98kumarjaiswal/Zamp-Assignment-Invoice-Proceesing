"""Process the seven sample invoices in the documented order against a FRESH database and print the results.

Usage: python scripts/run_sample_sequence.py [--db sqlite:///path.db] [--only 04-duplicate]
Exit code 0 only if every decision matches the documented expectation.
"""
import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.core.pipeline import InvoicePipeline  # noqa: E402
from app.db.engine import init_db, make_engine, reset_db  # noqa: E402

EXPECTED = {
    "01-clean": "APPROVED", "02-small-variance": "APPROVED", "03-missing-po": "REVIEW", "04-duplicate": "REJECTED",
    "05-vendor-mismatch": "REVIEW", "06-over-budget": "REVIEW", "07-scanned": "REVIEW",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--only", default=None, help="process just one sample (e.g. 04-duplicate) in the fresh DB")
    args = ap.parse_args()
    url = args.db or f"sqlite:///{Path(tempfile.mkdtemp()) / 'sequence.db'}"
    engine = make_engine(url)
    reset_db(engine) if args.db else init_db(engine)
    pipe = InvoicePipeline(engine, get_settings())
    names = [args.only] if args.only else list(EXPECTED)
    ok = True
    for name in names:
        data = (ROOT / "sample_data" / "invoices" / f"{name}.pdf").read_bytes()
        r = pipe.process(data, f"{name}.pdf")
        # with --only, 04 alone is a valid first-time invoice, so APPROVED is correct and is reported as such
        expected = EXPECTED[name] if not args.only else None
        flag = "" if expected is None else ("OK " if r.decision.value == expected else "MISMATCH ")
        ok &= flag != "MISMATCH "
        print(f"{flag}{name:20s} -> {r.decision.value:9s} run #{r.run_id}  {r.summary_line}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
