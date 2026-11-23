from decimal import Decimal

import pytest

from tests.conftest import check, pdf, sample


def run(pipe, data, name="inv.pdf"):
    return pipe.process(data, name)


# ------------------------------------------------------------------ the happy path and tolerance
def test_clean_invoice_approved(pipe):
    r = run(pipe, pdf())
    assert r.decision.value == "APPROVED" and r.matched_po == "PO-24001"
    assert all(c.status.value in ("PASS", "WARN") for c in r.checks)
    assert len(r.stages) == 10 and all(s.n == i + 1 for i, s in enumerate(r.stages))


def test_small_variance_within_tolerance_approved_with_note(pipe):
    r = run(pipe, sample("02-small-variance"))
    assert r.decision.value == "APPROVED"
    assert check(r, "price_variance").status.value == "WARN" and check(r, "po_balance").status.value == "WARN"


def test_price_beyond_tolerance_goes_to_review(pipe):
    r = run(pipe, pdf("02-small-variance", lines=[("Copy paper A4 (box)", 200, "4.40"), ("Toner cartridge", 10, "62.00")]))
    assert r.decision.value == "REVIEW" and check(r, "price_variance").status.value == "FAIL"


def test_quantity_above_po_goes_to_review(pipe):
    r = run(pipe, pdf(lines=[("Industrial gloves (carton)", 120, "12.50"), ("Safety goggles", 50, "8.40")]))
    assert r.decision.value == "REVIEW" and check(r, "qty_vs_po").status.value == "FAIL"


# ------------------------------------------------------------------ PO matching
def test_missing_po_review_with_suggestion_not_applied(pipe):
    r = run(pipe, sample("03-missing-po"))
    assert r.decision.value == "REVIEW" and r.matched_po is None
    assert "PO-24003" in check(r, "po_reference").detail and r.po_snapshot["suggested_pos"][0]["po_number"] == "PO-24003"


def test_unknown_po_number_review(pipe):
    r = run(pipe, pdf(po="PO-99999"))
    assert r.decision.value == "REVIEW" and "does not exist" in check(r, "po_reference").detail


def test_ambiguous_po_never_guessed(pipe):
    r = run(pipe, pdf(po="PO-24001 / PO-24008"))
    assert r.decision.value == "REVIEW" and r.matched_po is None and "multiple" in check(r, "po_reference").detail


def test_vendor_mismatch_review(pipe):
    r = run(pipe, sample("05-vendor-mismatch"))
    assert r.decision.value == "REVIEW" and check(r, "po_vendor").status.value == "FAIL"


def test_unknown_vendor_review(pipe):
    r = run(pipe, pdf(vendor="Shadow Traders Ltd"))
    assert r.decision.value == "REVIEW" and check(r, "vendor_identity").status.value == "FAIL"


def test_vendor_with_different_legal_suffix_is_flagged_not_silent(pipe):
    r = run(pipe, pdf("01-clean", vendor="Northwind Industrial Supplies LLC"))
    assert check(r, "vendor_identity").status.value == "WARN"


def test_blocked_vendor_rejected(pipe):
    r = run(pipe, pdf(vendor="Zenith Consulting Group LLC", po="PO-24014", tax_rate=0, lines=[("Strategy workshop (day)", 3, "4000.00")]))
    assert r.decision.value == "REJECTED" and check(r, "vendor_status").status.value == "REJECT"


def test_inactive_vendor_review(pipe):
    r = run(pipe, pdf(vendor="Cobalt Print & Design Co", po="PO-24013", tax_rate=0, lines=[("Banner printing", 50, "20.00")]))
    assert r.decision.value == "REVIEW" and check(r, "vendor_status").status.value == "FAIL"


def test_closed_po_review(pipe):
    r = run(pipe, pdf(po="PO-24008", lines=[("Hi-vis safety vest", 80, "14.00")]))
    assert r.decision.value == "REVIEW" and check(r, "po_status").status.value == "FAIL"


def test_currency_mismatch_with_po_review(pipe):
    r = run(pipe, pdf(currency="EUR"))
    assert r.decision.value == "REVIEW" and check(r, "po_currency").status.value == "FAIL"


def test_line_not_on_po_review(pipe):
    r = run(pipe, pdf(lines=[("Industrial gloves (carton)", 100, "12.50"), ("Gold plated stapler", 50, "8.40")]))
    assert r.decision.value == "REVIEW" and check(r, "line_match").status.value == "FAIL"


def test_tax_rate_differs_from_po_review(pipe):
    r = run(pipe, pdf(tax_rate=10))
    assert r.decision.value == "REVIEW" and check(r, "tax_rate_po").status.value == "FAIL"


# ------------------------------------------------------------------ balance
def test_over_budget_review(pipe):
    r = run(pipe, sample("06-over-budget"))
    assert r.decision.value == "REVIEW" and check(r, "po_balance").status.value == "FAIL"
    assert Decimal(r.calc["po_remaining"]) == Decimal("120000.00")


def test_cumulative_commitments_reduce_remaining_balance(pipe):
    first = run(pipe, pdf("01-clean"))
    assert first.decision.value == "APPROVED"
    second = run(pipe, pdf("01-clean", invoice_no="INV-10999"))   # same PO now fully used
    assert second.decision.value == "REVIEW" and check(second, "po_balance").status.value == "FAIL"


def test_split_billing_partial_invoices_against_one_po(pipe):
    a = run(pipe, pdf(vendor="Brightline Office Solutions Inc", po="PO-24011", tax_rate=7, invoice_no="B-1",
                      lines=[("Ergonomic office chair", 10, "150.00")]))
    b = run(pipe, pdf(vendor="Brightline Office Solutions Inc", po="PO-24011", tax_rate=7, invoice_no="B-2",
                      lines=[("Ergonomic office chair", 5, "150.00"), ("Standing desk", 3, "200.00")]))
    # PO 4000 net: 1000 pre-committed + 1500 (A) + 1350 (B) = 3850, leaving 150
    assert a.decision.value == "APPROVED" and b.decision.value == "APPROVED"
    c = run(pipe, pdf(vendor="Brightline Office Solutions Inc", po="PO-24011", tax_rate=7, invoice_no="B-3",
                      lines=[("Ergonomic office chair", 5, "150.00")]))
    assert c.decision.value == "REVIEW"


# ------------------------------------------------------------------ data validation
def test_missing_invoice_number_review(pipe):
    r = run(pipe, pdf(invoice_no=None))
    assert r.decision.value == "REVIEW" and check(r, "required_fields").status.value == "FAIL"
    assert check(r, "dup_key_approved").status.value == "UNCERTAIN"


@pytest.mark.parametrize("bad", ["2026-02-31", "03/04/2026", "2999-01-01"])
def test_invalid_or_future_dates_review(pipe, bad):
    r = run(pipe, pdf(invoice_date=bad))
    assert r.decision.value == "REVIEW" and check(r, "invoice_date").status.value == "FAIL"


def test_due_date_before_invoice_date_review(pipe):
    r = run(pipe, pdf(due_date="2026-10-01"))
    assert r.decision.value == "REVIEW" and check(r, "due_date").status.value == "FAIL"


def test_incorrect_total_review(pipe):
    r = run(pipe, pdf(printed={"total": "1900.00"}))
    assert r.decision.value == "REVIEW" and check(r, "total_calc").status.value == "FAIL"


def test_incorrect_line_total_review(pipe):
    r = run(pipe, pdf(printed={"line_amounts": ["1250.00", "430.00"]}))
    assert r.decision.value == "REVIEW" and check(r, "line_math").status.value == "FAIL"


def test_incorrect_tax_amount_review(pipe):
    r = run(pipe, pdf(printed={"tax": "150.00", "total": "1820.00"}))
    assert r.decision.value == "REVIEW" and check(r, "tax_calc").status.value == "FAIL"


def test_discount_is_applied(pipe):
    r = run(pipe, pdf(discount="70.00"))   # net 1600 -> within PO, tax 128 -> total 1728
    assert Decimal(r.calc["net_amount"]) == Decimal("1600.00") and Decimal(r.calc["expected_total"]) == Decimal("1728.00")
    assert r.decision.value == "APPROVED"


def test_invalid_currency_review(pipe):
    r = run(pipe, pdf(currency="XYZ"))
    assert r.decision.value == "REVIEW" and check(r, "currency_valid").status.value == "FAIL"


def test_missing_currency_never_guessed(pipe):
    r = run(pipe, pdf(currency=None, layout="A"))
    assert r.decision.value == "REVIEW" and r.invoice.currency.value is None


def test_missing_total_never_invented(pipe):
    r = run(pipe, pdf(printed={"omit_total": True}))  # no total printed at all
    assert r.decision.value == "REVIEW" and r.invoice.total.value is None


def test_ocr_invoice_never_auto_approved(pipe):
    r = run(pipe, sample("07-scanned"))
    assert r.decision.value == "REVIEW" and check(r, "ocr_verified").status.value == "UNCERTAIN"
    others = [c for c in r.checks if c.id != "ocr_verified"]
    assert all(c.status.value in ("PASS", "WARN") for c in others)   # OCR read everything correctly; policy alone blocks


# ------------------------------------------------------------------ file-level problems
@pytest.mark.parametrize("data,name", [(b"", "empty.pdf"), (b"%PDF-1.4 garbage", "corrupt.pdf"), (b"not a pdf", "x.pdf")])
def test_unprocessable_files_go_to_review_and_are_audited(pipe, data, name):
    r = pipe.process(data, name)
    assert r.decision.value == "REVIEW" and r.run_id and r.errors
    failed_at = next(i for i, s in enumerate(r.stages) if s.status == "failed")
    assert all(s.status == "skipped" for s in r.stages[failed_at + 1:8]) and len(r.stages) == 10


def test_oversized_file_rejected_politely(eng):
    import dataclasses
    from app.config import get_settings
    from app.core.pipeline import InvoicePipeline
    p = InvoicePipeline(eng, dataclasses.replace(get_settings(), max_file_mb=0))
    r = p.process(pdf(), "big.pdf")
    assert r.decision.value == "REVIEW" and any("limit" in e for e in r.errors)


# ------------------------------------------------------------------ duplicates
def test_duplicate_of_approved_invoice_rejected(pipe):
    first = pipe.process(sample("01-clean"), "01.pdf")
    dup = pipe.process(sample("04-duplicate"), "04.pdf")
    assert first.decision.value == "APPROVED" and dup.decision.value == "REJECTED"
    assert dup.duplicate_of_run == first.run_id and check(dup, "dup_key_approved").status.value == "REJECT"
    assert check(dup, "dup_hash_approved").status.value == "PASS"     # file differs, detection came from vendor+number


def test_sample_04_alone_is_a_valid_first_submission(pipe):
    assert pipe.process(sample("04-duplicate"), "04.pdf").decision.value == "APPROVED"


def test_renamed_identical_file_is_still_a_duplicate(pipe):
    data = pdf()
    assert pipe.process(data, "original.pdf").decision.value == "APPROVED"
    again = pipe.process(data, "totally-different-name-FINAL.pdf")
    assert again.decision.value == "REJECTED" and check(again, "dup_hash_approved").status.value == "REJECT"


def test_invoice_number_formatting_does_not_bypass_detection(pipe):
    pipe.process(pdf(invoice_no="INV-0042"), "a.pdf")
    r = pipe.process(pdf(invoice_no="inv42", invoice_date="2026-11-04"), "b.pdf")
    assert r.decision.value == "REJECTED"


def test_same_invoice_number_different_vendor_is_not_a_duplicate(pipe):
    pipe.process(pdf(invoice_no="1001"), "a.pdf")
    r = pipe.process(pdf("02-small-variance", invoice_no="1001"), "b.pdf")
    assert r.decision.value == "APPROVED"


def test_resubmitting_unapproved_invoice_is_flagged_not_rejected(pipe):
    data = sample("03-missing-po")
    pipe.process(data, "a.pdf")
    r = pipe.process(data, "b.pdf")
    assert r.decision.value == "REVIEW" and check(r, "dup_prior_open").status.value == "FAIL"


def test_same_vendor_amount_date_different_number_flagged(pipe):
    pipe.process(pdf(invoice_no="A-100"), "a.pdf")
    r = pipe.process(pdf("01-clean", invoice_no="Z-999", lines=[("Industrial gloves (carton)", 100, "12.50"), ("Safety goggles", 50, "8.40")]), "b.pdf")
    assert check(r, "dup_amount_date").status.value == "FAIL" and r.decision.value != "APPROVED"


# ------------------------------------------------------------------ explanations
def test_every_decision_is_explained(pipe):
    for data in (pdf(), sample("03-missing-po")):
        r = pipe.process(data, "x.pdf")
        for part in ("What was checked", "What passed"):
            assert part in r.explanation
        assert r.summary_line and (r.decision.value == "APPROVED" or ("What failed" in r.explanation and r.next_actions))
