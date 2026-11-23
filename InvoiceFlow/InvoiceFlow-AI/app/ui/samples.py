"""Catalogue of the bundled demo invoices (shown in the 'Try a sample' picker)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_DIR = ROOT / "sample_data" / "invoices"

SAMPLES = [
    ("01-clean.pdf", "Clean invoice, matches PO-24001 exactly", "APPROVED"),
    ("02-small-variance.pdf", "Unit price 0.95% above PO, inside the 2% tolerance", "APPROVED"),
    ("03-missing-po.pdf", "No PO reference on the invoice", "REVIEW"),
    ("04-duplicate.pdf", "Re-formatted copy of invoice INV-10231 (process 01 first)", "REJECTED"),
    ("05-vendor-mismatch.pdf", "Brightline invoice quoting a PO that belongs to Orion", "REVIEW"),
    ("06-over-budget.pdf", "Invoice exceeds the remaining PO balance", "REVIEW"),
    ("07-scanned.pdf", "Image-only scan, read by OCR", "REVIEW"),
]


def sample_bytes(name: str) -> bytes:
    return (SAMPLE_DIR / name).read_bytes()
