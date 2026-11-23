"""Stages 5 and 7 - data validation and deterministic financial validation.

Rounding rule (single, documented): every computed money value is rounded half-up to 2 decimals.
Arithmetic tolerance: |printed - computed| <= ARITHMETIC_TOLERANCE (default 0.02) per comparison.
Price tolerance: |invoice unit price - PO unit price| / PO unit price <= PRICE_TOLERANCE_PCT (default 2%).
Balance tolerance: invoice net may exceed the PO remaining balance by at most PRICE_TOLERANCE_PCT of the PO value.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.config import Settings
from app.core.models import Check, CheckStatus as C, InvoiceData
from app.core.utils import SUPPORTED_CURRENCIES, fmt_money, parse_date, q2

CRITICAL = ("invoice_number", "invoice_date", "total", "currency")
LABELS = {"invoice_number": "invoice number", "invoice_date": "invoice date", "total": "invoice total",
          "currency": "currency", "vendor_name": "vendor name"}


def _dec(f) -> Decimal | None:
    try:
        return Decimal(f.value) if f.value not in (None, "") else None
    except Exception:
        return None


def validate_data(inv: InvoiceData, settings: Settings, used_ocr: bool, ocr_conf: float | None,
                  ocr_verified_by: str | None) -> list[Check]:
    checks: list[Check] = []

    # required fields present
    missing = [LABELS[k] for k in ("vendor_name", *CRITICAL) if not getattr(inv, k).value]
    if missing:
        checks.append(Check(id="required_fields", category="data", name="Required fields present", status=C.FAIL,
                            detail="Missing from the document: " + ", ".join(missing) + ". Missing values are never guessed.",
                            expected="vendor, invoice number, invoice date, currency, total",
                            actual="missing " + ", ".join(missing),
                            next_action="Ask the vendor for a corrected invoice, or have a reviewer supply the value from the document."))
    else:
        checks.append(Check(id="required_fields", category="data", name="Required fields present", status=C.PASS,
                            detail="Vendor, invoice number, invoice date, currency and total were all found."))

    if not inv.line_items:
        checks.append(Check(id="line_items", category="data", name="Line items extracted", status=C.FAIL,
                            detail="No itemised lines could be read, so the invoice cannot be verified line by line.",
                            next_action="Review the document manually."))
    else:
        checks.append(Check(id="line_items", category="data", name="Line items extracted", status=C.PASS,
                            detail=f"{len(inv.line_items)} line item(s) extracted."))

    # dates
    today = date.today()
    d_inv, d_due = None, None
    if inv.invoice_date.value:
        d_inv, prob = parse_date(inv.invoice_date.value)
        if prob or inv.invoice_date.problem:
            checks.append(Check(id="invoice_date", category="data", name="Invoice date valid", status=C.FAIL,
                                detail=f"Invoice date problem: {prob or inv.invoice_date.problem}.",
                                evidence=inv.invoice_date.evidence, next_action="Confirm the correct date with the vendor."))
        elif d_inv > today and not settings.allow_future_dates:
            checks.append(Check(id="invoice_date", category="data", name="Invoice date valid", status=C.FAIL,
                                detail=f"Invoice date {d_inv.isoformat()} is in the future.", evidence=inv.invoice_date.evidence,
                                next_action="Confirm the date; do not pay before the invoice is valid."))
        else:
            checks.append(Check(id="invoice_date", category="data", name="Invoice date valid", status=C.PASS,
                                detail=f"Invoice date {d_inv.isoformat()}.", evidence=inv.invoice_date.evidence))
    elif inv.invoice_date.problem:
        checks.append(Check(id="invoice_date", category="data", name="Invoice date valid", status=C.FAIL,
                            detail=f"Invoice date problem: {inv.invoice_date.problem}.", evidence=inv.invoice_date.evidence,
                            next_action="Confirm the correct date with the vendor."))
    if inv.due_date.value or inv.due_date.problem:
        d_due, prob = parse_date(inv.due_date.value) if inv.due_date.value else (None, inv.due_date.problem)
        if prob:
            checks.append(Check(id="due_date", category="data", name="Due date valid", status=C.FAIL,
                                detail=f"Due date problem: {prob}.", evidence=inv.due_date.evidence))
        elif d_inv and d_due < d_inv:
            checks.append(Check(id="due_date", category="data", name="Due date valid", status=C.FAIL,
                                detail=f"Due date {d_due} is earlier than the invoice date {d_inv}.", evidence=inv.due_date.evidence,
                                next_action="Confirm the payment terms with the vendor."))
        else:
            checks.append(Check(id="due_date", category="data", name="Due date valid", status=C.PASS,
                                detail=f"Due date {d_due.isoformat()}."))

    # currency
    cur = inv.currency.value
    if cur:
        if cur not in SUPPORTED_CURRENCIES or inv.currency.problem:
            checks.append(Check(id="currency_valid", category="data", name="Currency code valid", status=C.FAIL,
                                detail=inv.currency.problem or f"'{cur}' is not a supported currency code.",
                                actual=cur, evidence=inv.currency.evidence, next_action="Confirm the billing currency with the vendor."))
        else:
            checks.append(Check(id="currency_valid", category="data", name="Currency code valid", status=C.PASS,
                                detail=f"{cur} is a supported currency.", evidence=inv.currency.evidence))

    # conflicting values inside the document
    conflicts = [f"{LABELS.get(k, k)}: {getattr(inv, k).problem}" for k in ("total", "subtotal", "tax_amount")
                 if getattr(inv, k).problem]
    if conflicts:
        checks.append(Check(id="field_conflict", category="data", name="No conflicting values in document", status=C.FAIL,
                            detail="; ".join(conflicts), next_action="Review the document; it contains contradictory figures."))

    # amounts parse
    for k in ("total", "subtotal", "tax_amount"):
        f = getattr(inv, k)
        if f.value is None and f.problem and "conflicting" not in f.problem:
            checks.append(Check(id=f"{k}_parse", category="data", name=f"{k.replace('_', ' ').title()} readable", status=C.FAIL,
                                detail=f"Could not read {k.replace('_', ' ')}: {f.problem}.", evidence=f.evidence))

    # confidence on critical fields
    low = [(LABELS[k], getattr(inv, k).confidence) for k in CRITICAL
           if getattr(inv, k).value and getattr(inv, k).confidence < settings.min_field_confidence]
    if low:
        checks.append(Check(id="confidence", category="extraction", name="Extraction confidence", status=C.UNCERTAIN,
                            detail="Low-confidence critical fields: " + ", ".join(f"{n} ({c:.0%})" for n, c in low)
                                   + f". Threshold is {settings.min_field_confidence:.0%}.",
                            next_action="A reviewer must verify these values against the document."))
    else:
        worst = min((getattr(inv, k).confidence for k in CRITICAL if getattr(inv, k).value), default=0)
        checks.append(Check(id="confidence", category="extraction", name="Extraction confidence",
                            status=C.PASS if worst >= settings.min_field_confidence else C.SKIPPED,
                            detail=f"All present critical fields are at or above {settings.min_field_confidence:.0%} confidence "
                                   f"(lowest: {worst:.0%})." if worst else "No critical fields to score."))

    # OCR policy
    if used_ocr:
        if ocr_verified_by:
            checks.append(Check(id="ocr_verified", category="extraction", name="Scanned document verified by a person",
                                status=C.PASS, detail=f"Values read by OCR were verified against the document by {ocr_verified_by}."))
        else:
            checks.append(Check(id="ocr_verified", category="extraction", name="Scanned document verified by a person",
                                status=C.UNCERTAIN,
                                detail=f"This document was read by OCR (mean confidence {ocr_conf or 0:.0f}%). Policy: OCR-read invoices are never auto-approved, because a single misread digit changes a payment.",
                                next_action="A reviewer compares the extracted values with the scan, then re-validates with the 'verified' attestation."))
    return checks


def _near(a: Decimal, b: Decimal, tol: Decimal) -> bool:
    return abs(a - b) <= tol


def validate_financials(inv: InvoiceData, po: dict | None, line_matches: list[dict], settings: Settings) -> tuple[list[Check], dict]:
    """Deterministic arithmetic and PO-based checks. Returns (checks, calc) - calc holds every number used."""
    tol = settings.arithmetic_tolerance
    checks: list[Check] = []
    calc: dict = {"rounding": "half-up, 2 dp", "arithmetic_tolerance": str(tol),
                  "price_tolerance_pct": str(settings.price_tolerance_pct)}
    items = inv.line_items

    # 1. qty x unit price = amount
    bad = []
    for it in items:
        if None in (it.quantity, it.unit_price, it.amount):
            bad.append(f"'{it.description}' (unreadable numbers)")
        elif not _near(q2(it.quantity * it.unit_price), it.amount, Decimal("0.01")):
            bad.append(f"'{it.description}': {it.quantity} x {it.unit_price} = {q2(it.quantity * it.unit_price)}, invoice shows {it.amount}")
    if not items:
        checks.append(Check(id="line_math", category="financial", name="Quantity x unit price = line total", status=C.SKIPPED,
                            detail="No line items to check."))
    elif bad:
        checks.append(Check(id="line_math", category="financial", name="Quantity x unit price = line total", status=C.FAIL,
                            detail="Line arithmetic does not reconcile: " + " | ".join(bad),
                            next_action="Ask the vendor for a corrected invoice."))
    else:
        checks.append(Check(id="line_math", category="financial", name="Quantity x unit price = line total", status=C.PASS,
                            detail=f"All {len(items)} line(s) multiply out correctly (to the cent)."))

    lines_sum = q2(sum((it.amount for it in items if it.amount is not None), Decimal(0))) if items else None
    subtotal, discount, tax_amt, total = _dec(inv.subtotal), _dec(inv.discount), _dec(inv.tax_amount), _dec(inv.total)
    calc["lines_sum"] = str(lines_sum) if lines_sum is not None else None

    # 2. lines -> subtotal
    derived = False
    if subtotal is None and lines_sum is not None:
        subtotal, derived = lines_sum, True
        calc["subtotal_derived"] = True
    if subtotal is not None and lines_sum is not None:
        if _near(lines_sum, subtotal, tol):
            checks.append(Check(id="subtotal", category="financial", name="Line totals add up to subtotal",
                                status=C.WARN if derived else C.PASS,
                                detail=(f"No subtotal printed; derived {fmt_money(subtotal)} from the lines." if derived
                                        else f"Sum of lines {fmt_money(lines_sum)} equals subtotal {fmt_money(subtotal)}."),
                                expected=str(lines_sum), actual=str(subtotal), evidence=inv.subtotal.evidence))
        else:
            checks.append(Check(id="subtotal", category="financial", name="Line totals add up to subtotal", status=C.FAIL,
                                detail=f"Sum of lines is {fmt_money(lines_sum)} but the invoice subtotal is {fmt_money(subtotal)} (difference {fmt_money(abs(lines_sum - subtotal))}).",
                                expected=str(lines_sum), actual=str(subtotal), evidence=inv.subtotal.evidence,
                                next_action="Ask the vendor for a corrected invoice."))
    else:
        checks.append(Check(id="subtotal", category="financial", name="Line totals add up to subtotal", status=C.SKIPPED,
                            detail="Subtotal or line items missing."))

    # 3. discount -> net
    disc = discount or Decimal("0")
    net = q2(subtotal - disc) if subtotal is not None else None
    calc.update(subtotal=str(subtotal) if subtotal is not None else None, discount=str(disc),
                net_amount=str(net) if net is not None else None)
    if discount is not None:
        checks.append(Check(id="discount", category="financial", name="Discount applied", status=C.PASS if net is not None and net >= 0 else C.FAIL,
                            detail=f"Discount {fmt_money(disc)} taken off subtotal gives net {fmt_money(net)}.", evidence=inv.discount.evidence))

    # 4. tax
    rate_printed = Decimal(inv.tax_rate.value) if inv.tax_rate.value else None
    if net is None:
        checks.append(Check(id="tax_calc", category="financial", name="Tax calculation", status=C.SKIPPED, detail="Net amount unknown."))
    else:
        if tax_amt is None:
            if rate_printed and rate_printed > 0:
                checks.append(Check(id="tax_calc", category="financial", name="Tax calculation", status=C.FAIL,
                                    detail=f"A {rate_printed}% tax rate is shown but no tax amount could be read.", evidence=inv.tax_rate.evidence))
            else:
                tax_amt = Decimal("0")
                checks.append(Check(id="tax_calc", category="financial", name="Tax calculation", status=C.WARN,
                                    detail="No tax line on the invoice; treated as zero tax (not invented: total is checked against this)."))
        if tax_amt is not None and not any(c.id == "tax_calc" for c in checks):
            if rate_printed is not None:
                expected_tax = q2(net * rate_printed / 100)
                ok = _near(expected_tax, tax_amt, tol)
                checks.append(Check(id="tax_calc", category="financial", name="Tax calculation",
                                    status=C.PASS if ok else C.FAIL,
                                    detail=(f"{rate_printed}% of {fmt_money(net)} = {fmt_money(expected_tax)}; invoice shows {fmt_money(tax_amt)}."),
                                    expected=str(expected_tax), actual=str(tax_amt), evidence=inv.tax_rate.evidence,
                                    next_action=None if ok else "Ask the vendor to correct the tax amount."))
            else:
                implied = (tax_amt / net * 100).quantize(Decimal("0.01")) if net else Decimal(0)
                calc["implied_tax_rate"] = str(implied)
                checks.append(Check(id="tax_calc", category="financial", name="Tax calculation", status=C.WARN,
                                    detail=f"Tax rate not printed; implied rate is {implied}%."))
        calc["tax_amount"] = str(tax_amt) if tax_amt is not None else None

    # 5. total
    if net is not None and tax_amt is not None and total is not None:
        expected_total = q2(net + tax_amt)
        calc["expected_total"] = str(expected_total)
        if _near(expected_total, total, tol):
            checks.append(Check(id="total_calc", category="financial", name="Invoice total reconciles", status=C.PASS,
                                detail=f"{fmt_money(net)} net + {fmt_money(tax_amt)} tax = {fmt_money(expected_total)}; invoice total {fmt_money(total)}.",
                                expected=str(expected_total), actual=str(total), evidence=inv.total.evidence))
        else:
            checks.append(Check(id="total_calc", category="financial", name="Invoice total reconciles", status=C.FAIL,
                                detail=f"Net + tax = {fmt_money(expected_total)} but the invoice total is {fmt_money(total)} (difference {fmt_money(abs(expected_total - total))}).",
                                expected=str(expected_total), actual=str(total), evidence=inv.total.evidence,
                                next_action="Ask the vendor for a corrected invoice."))
    else:
        checks.append(Check(id="total_calc", category="financial", name="Invoice total reconciles", status=C.SKIPPED,
                            detail="Total, net or tax missing, so the total cannot be verified."))

    # ---------------- PO based checks
    skip = "No single valid PO was matched, so this PO comparison was not performed."
    if not po or net is None:
        for cid, nm in (("tax_rate_po", "Tax rate matches PO"), ("price_variance", "Unit prices match PO (within tolerance)"),
                        ("qty_vs_po", "Quantities within PO"), ("po_balance", "Within remaining PO balance")):
            checks.append(Check(id=cid, category="financial", name=nm, status=C.SKIPPED, detail=skip))
        return checks, calc

    po_tax = po["tax_rate"]
    inv_rate = rate_printed if rate_printed is not None else (Decimal(calc["implied_tax_rate"]) if "implied_tax_rate" in calc else
                                                              (Decimal(0) if tax_amt == 0 else None))
    if inv_rate is None:
        checks.append(Check(id="tax_rate_po", category="financial", name="Tax rate matches PO", status=C.UNCERTAIN,
                            detail="Invoice tax rate could not be determined."))
    elif abs(inv_rate - po_tax) > Decimal("0.05"):
        checks.append(Check(id="tax_rate_po", category="financial", name="Tax rate matches PO", status=C.FAIL,
                            detail=f"Invoice applies {inv_rate}% tax but the PO specifies {po_tax}%.", expected=f"{po_tax}%",
                            actual=f"{inv_rate}%", next_action="Ask the vendor to correct the tax treatment."))
    else:
        checks.append(Check(id="tax_rate_po", category="financial", name="Tax rate matches PO", status=C.PASS,
                            detail=f"Tax rate {inv_rate}% equals the PO rate.", expected=f"{po_tax}%", actual=f"{inv_rate}%"))

    tol_pct = settings.price_tolerance_pct
    price_fail, price_warn, qty_fail, rows = [], [], [], []
    for m in line_matches:
        if m["po_index"] is None:
            continue
        it, pl = items[m["inv_index"]], po["lines"][m["po_index"]]
        if it.unit_price is None or it.quantity is None:
            continue
        var = (it.unit_price - pl["unit_price"]) / pl["unit_price"] * 100
        rows.append({"description": it.description, "po_line": m["po_line_no"], "invoice_unit_price": str(it.unit_price),
                     "po_unit_price": str(pl["unit_price"]), "variance_pct": str(var.quantize(Decimal("0.01"))),
                     "invoice_qty": str(it.quantity), "po_qty": str(pl["quantity"])})
        if abs(var) > tol_pct:
            price_fail.append(f"'{it.description}' {it.unit_price} vs PO {pl['unit_price']} ({var:+.2f}%)")
        elif var != 0:
            price_warn.append(f"'{it.description}' {it.unit_price} vs PO {pl['unit_price']} ({var:+.2f}%)")
        if it.quantity > pl["quantity"]:
            qty_fail.append(f"'{it.description}' qty {it.quantity} exceeds PO qty {pl['quantity']}")
    calc["line_comparison"] = rows
    if price_fail:
        checks.append(Check(id="price_variance", category="financial", name="Unit prices match PO (within tolerance)", status=C.FAIL,
                            detail=f"Price variance beyond +/-{tol_pct}%: " + "; ".join(price_fail),
                            next_action="Ask the vendor to explain the price change, or have Procurement approve a PO amendment."))
    elif price_warn:
        checks.append(Check(id="price_variance", category="financial", name="Unit prices match PO (within tolerance)", status=C.WARN,
                            detail=f"Small variance, inside the +/-{tol_pct}% tolerance: " + "; ".join(price_warn)))
    else:
        checks.append(Check(id="price_variance", category="financial", name="Unit prices match PO (within tolerance)", status=C.PASS,
                            detail="All unit prices equal the PO prices."))
    checks.append(Check(id="qty_vs_po", category="financial", name="Quantities within PO",
                        status=C.FAIL if qty_fail else C.PASS,
                        detail=("; ".join(qty_fail) if qty_fail else "No invoiced quantity exceeds the PO quantity."),
                        next_action="Over-billing or a missing PO amendment: confirm with Procurement." if qty_fail else None))

    remaining, allowance = po["remaining"], q2(po["amount_net"] * tol_pct / 100)
    calc.update(po_amount=str(po["amount_net"]), po_committed=str(po["committed_net"]), po_remaining=str(remaining),
                balance_allowance=str(allowance), balance_cap_minor=int((po["amount_net"] + allowance) * 100))
    if net <= remaining:
        checks.append(Check(id="po_balance", category="financial", name="Within remaining PO balance", status=C.PASS,
                            detail=f"Net {fmt_money(net, po['currency'])} is within the remaining balance {fmt_money(remaining, po['currency'])}.",
                            expected=f"<= {remaining}", actual=str(net)))
    elif net <= remaining + allowance:
        checks.append(Check(id="po_balance", category="financial", name="Within remaining PO balance", status=C.WARN,
                            detail=f"Net {fmt_money(net, po['currency'])} exceeds the remaining balance {fmt_money(remaining, po['currency'])} by {fmt_money(net - remaining)}, inside the {tol_pct}% tolerance ({fmt_money(allowance)}).",
                            expected=f"<= {remaining} (+{allowance} tolerance)", actual=str(net)))
    else:
        checks.append(Check(id="po_balance", category="financial", name="Within remaining PO balance", status=C.FAIL,
                            detail=f"Net {fmt_money(net, po['currency'])} exceeds the remaining PO balance {fmt_money(remaining, po['currency'])} by {fmt_money(net - remaining)}; tolerance only allows {fmt_money(allowance)}.",
                            expected=f"<= {remaining} (+{allowance} tolerance)", actual=str(net),
                            next_action="Procurement must increase the PO or approve the overrun before payment."))
    return checks, calc
