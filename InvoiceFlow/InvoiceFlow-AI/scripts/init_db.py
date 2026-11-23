"""Create tables and seed demo vendors/POs in whatever database DATABASE_URL points to (idempotent).

Usage:
  python scripts/init_db.py            # uses DATABASE_URL / .env, else local SQLite
  python scripts/init_db.py --reset    # DROP everything and re-seed (destructive!)
The app also does this automatically on first start, so running this is optional.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text  # noqa: E402

from app.db import schema as S  # noqa: E402
from app.db.engine import database_label, init_db, make_engine, reset_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop all tables first (destructive)")
    args = ap.parse_args()
    eng = make_engine()
    with eng.connect() as c:
        c.execute(text("SELECT 1"))
    print("Connected:", database_label(eng))
    reset_db(eng) if args.reset else init_db(eng)
    with eng.connect() as c:
        for t in (S.vendors, S.purchase_orders, S.runs):
            print(f"  {t.name}: {c.execute(select(func.count()).select_from(t)).scalar()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
