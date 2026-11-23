"""Stage 8 - duplicate detection with several independent indicators."""
from __future__ import annotations

from app.core.models import Check, CheckStatus as C, InvoiceData
from app.core.utils import normalize_invoice_number, normalize_name, to_minor
from decimal import Decimal
from app.db import repo


def detect_duplicates(inv: InvoiceData, vendor_id: str | None, doc_hash: str, conn, exclude_runs: list[int]) -> tuple[list[Check], int | None]:
    """Returns (checks, run_id_of_the_confirmed_duplicate)."""
    checks: list[Check] = []
    dup_run: int | None = None
    norm = normalize_invoice_number(inv.invoice_number.value)
    vendor_key = vendor_id or (f"NAME:{normalize_name(inv.vendor_name.value)}" if inv.vendor_name.value else None)

    # 1. identical file, already approved (renaming the file changes nothing - we hash the bytes)
    a = repo.approved_by_hash(conn, doc_hash)
    if a and a["run_id"] not in exclude_runs:
        dup_run = a["run_id"]
        checks.append(Check(id="dup_hash_approved", category="duplicate", name="Identical file already approved", status=C.REJECT,
                            detail=f"This exact file (SHA-256 {doc_hash[:12]}...) was already approved in run #{a['run_id']}, regardless of its file name.",
                            next_action="Do not pay again. Notify the vendor if they resubmitted by mistake."))
    else:
        checks.append(Check(id="dup_hash_approved", category="duplicate", name="Identical file already approved", status=C.PASS,
                            detail=f"No approved invoice has the same file hash ({doc_hash[:12]}...)."))

    # 2. same vendor + same normalised invoice number, already approved
    if norm and vendor_key and not vendor_key.startswith("NAME:"):
        a = repo.approved_by_key(conn, vendor_key, norm)
        if a and a["run_id"] not in exclude_runs:
            dup_run = dup_run or a["run_id"]
            checks.append(Check(id="dup_key_approved", category="duplicate", name="Same vendor + invoice number already approved",
                                status=C.REJECT,
                                detail=f"Vendor {vendor_key} already has approved invoice '{inv.invoice_number.value}' (normalised '{norm}') in run #{a['run_id']}. This is a confirmed duplicate even though the file differs.",
                                actual=norm, next_action="Do not pay again. Confirm with the vendor whether this is a resubmission."))
        else:
            checks.append(Check(id="dup_key_approved", category="duplicate", name="Same vendor + invoice number already approved",
                                status=C.PASS, detail=f"No approved invoice exists for this vendor with number '{norm}'."))
    else:
        checks.append(Check(id="dup_key_approved", category="duplicate", name="Same vendor + invoice number already approved",
                            status=C.UNCERTAIN if not norm else C.SKIPPED,
                            detail="Invoice number is missing, so number-based duplicate detection is impossible." if not norm
                            else "Vendor was not identified, so number-based duplicate detection against approved invoices could not run.",
                            next_action="Verify manually that this invoice has not been paid before."))

    # 3. earlier submissions that are NOT approved (possible, not confirmed)
    prior = repo.prior_runs_by_hash(conn, doc_hash, exclude_runs)
    key_prior = repo.prior_runs_by_key(conn, vendor_key, norm, exclude_runs) if (norm and vendor_key) else []
    seen = {p["id"]: p for p in prior} | {p["id"]: p for p in key_prior}
    seen = {k: v for k, v in seen.items() if v["decision"] != "APPROVED"}
    if seen:
        desc = ", ".join(f"#{k} ({v['decision']})" for k, v in sorted(seen.items()))
        checks.append(Check(id="dup_prior_open", category="duplicate", name="Earlier submission of the same invoice", status=C.FAIL,
                            detail=f"The same file or invoice number was submitted before and was not approved: run {desc}. It may be a resubmission.",
                            next_action="Check the earlier run before deciding; avoid working the same invoice twice."))
    else:
        checks.append(Check(id="dup_prior_open", category="duplicate", name="Earlier submission of the same invoice", status=C.PASS,
                            detail="No earlier unapproved submission with the same file or invoice number."))

    # 4. same vendor + amount + date, different number (possible re-numbered duplicate)
    total = inv.total.value
    if vendor_id and total and inv.invoice_date.value:
        near = [r for r in repo.approved_by_amount(conn, vendor_id, to_minor(Decimal(total)), inv.invoice_date.value)
                if r["run_id"] not in exclude_runs and normalize_invoice_number(r["invoice_number"]) != norm]
        if near:
            checks.append(Check(id="dup_amount_date", category="duplicate", name="Same vendor, amount and date", status=C.FAIL,
                                detail=f"Approved run #{near[0]['run_id']} (invoice '{near[0]['invoice_number']}') has the same vendor, total and date but a different invoice number.",
                                next_action="Possible re-numbered duplicate: confirm with the vendor."))
        else:
            checks.append(Check(id="dup_amount_date", category="duplicate", name="Same vendor, amount and date", status=C.PASS,
                                detail="No approved invoice has the same vendor, total and date."))
    return checks, dup_run
