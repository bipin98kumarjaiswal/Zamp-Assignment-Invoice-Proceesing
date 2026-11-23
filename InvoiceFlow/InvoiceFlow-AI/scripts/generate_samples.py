"""Regenerate sample_data/invoices/*.pdf and sample_data/data/*.csv|json (fictional data only).

Usage: python scripts/generate_samples.py
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.sample_gen import build_sample, sample_specs  # noqa: E402
from app.seed_data import PURCHASE_ORDERS, VENDORS  # noqa: E402


def main() -> None:
    inv_dir, data_dir = ROOT / "sample_data" / "invoices", ROOT / "sample_data" / "data"
    inv_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in sample_specs():
        (inv_dir / f"{name}.pdf").write_bytes(build_sample(name))
        print("wrote", inv_dir / f"{name}.pdf")
    with open(data_dir / "vendors.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "status", "country", "tax_id", "address"])
        w.writerows(VENDORS)
    with open(data_dir / "purchase_orders.json", "w") as f:
        json.dump([{"po_number": p[0], "vendor_id": p[1], "currency": p[2], "tax_rate_pct": p[3], "status": p[4],
                    "committed_net": p[5], "description": p[6],
                    "lines": [{"description": d, "quantity": q, "unit_price": u} for d, q, u in p[7]]}
                   for p in PURCHASE_ORDERS], f, indent=2)
    print("wrote reference data")


if __name__ == "__main__":
    main()
