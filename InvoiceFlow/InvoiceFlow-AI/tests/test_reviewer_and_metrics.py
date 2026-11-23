from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.core.metrics import compute_metrics, effective_runs, filter_runs
from app.db import repo
from tests.conftest import check, pdf, sample


def test_reviewer_assigns_po_and_engine_approves(pipe):
    r = pipe.process(sample("03-missing-po"), "03.pdf")
    child = pipe.revalidate(r.run_id, "Priya", "PO confirmed with vendor email thread", {"po_number": "PO-24003"})
    assert child.decision.value == "APPROVED" and child.parent_run_id == r.run_id
    assert child.invoice.po_numbers == ["PO-24003"] and child.invoice.po_evidence.startswith("Corrected by Priya")
    with pipe.engine.connect() as c:
        assert repo.get_run(c, r.run_id)["review_state"] == "RESOLVED"
        assert repo.list_review_actions(c, r.run_id)[0]["child_run_id"] == child.run_id


def test_reviewer_correction_cannot_launder_a_bad_invoice(pipe):
    """Wrong PO chosen by a reviewer: the rules still reject/review it - no bypass."""
    r = pipe.process(sample("03-missing-po"), "03.pdf")
    child = pipe.revalidate(r.run_id, "Sam", "guess", {"po_number": "PO-24010"})
    assert child.decision.value == "REVIEW" and check(child, "line_match").status.value == "FAIL"


def test_reviewer_cannot_edit_amounts(pipe):
    r = pipe.process(sample("06-over-budget"), "06.pdf")
    with pytest.raises(ValueError):
        pipe.revalidate(r.run_id, "Sam", "make it fit", {"total": "100.00"})


def test_over_budget_cannot_be_fixed_by_reviewer(pipe):
    r = pipe.process(sample("06-over-budget"), "06.pdf")
    child = pipe.revalidate(r.run_id, "Sam", "I looked at it and it is fine", {"vendor_name": "Meridian Software Services Pvt Ltd"})
    assert child.decision.value == "REVIEW" and check(child, "po_balance").status.value == "FAIL"


def test_justification_and_name_required(pipe):
    r = pipe.process(sample("03-missing-po"), "03.pdf")
    with pytest.raises(ValueError):
        pipe.revalidate(r.run_id, "Sam", "  ", {"po_number": "PO-24003"})
    with pytest.raises(ValueError):
        pipe.revalidate(r.run_id, "", "because", {"po_number": "PO-24003"})


def test_ocr_needs_explicit_attestation(pipe):
    r = pipe.process(sample("07-scanned"), "07.pdf")
    no = pipe.revalidate(r.run_id, "Ana", "re-checked", {})
    assert no.decision.value == "REVIEW" and check(no, "ocr_verified").status.value == "UNCERTAIN"
    yes = pipe.revalidate(no.run_id, "Ana", "compared amounts with the scan", {}, ocr_verified=True)
    assert yes.decision.value == "APPROVED" and check(yes, "ocr_verified").status.value == "PASS"


def test_revalidation_is_not_flagged_as_duplicate_of_its_own_parent(pipe):
    r = pipe.process(sample("03-missing-po"), "03.pdf")
    child = pipe.revalidate(r.run_id, "Priya", "ok", {"po_number": "PO-24003"})
    assert check(child, "dup_prior_open").status.value == "PASS"


def test_review_actions_are_audited(pipe, eng):
    r = pipe.process(sample("03-missing-po"), "03.pdf")
    repo.add_review_action(eng, r.run_id, "INFO_REQUESTED", "Lee", "asked vendor for PO", "INFO_REQUESTED")
    repo.add_review_action(eng, r.run_id, "REJECTED", "Lee", "vendor unresponsive", "REJECTED_BY_REVIEWER")
    with eng.connect() as c:
        assert [a["action"] for a in repo.list_review_actions(c, r.run_id)] == ["INFO_REQUESTED", "REJECTED"]
        assert [a["event"] for a in repo.audit_for_run(c, r.run_id)][-2:] == ["REVIEW_INFO_REQUESTED", "REVIEW_REJECTED"]


# ------------------------------------------------------------------ dashboard maths
def seed_all(pipe):
    for n in ("01-clean", "02-small-variance", "03-missing-po", "04-duplicate", "05-vendor-mismatch", "06-over-budget", "07-scanned"):
        pipe.process(sample(n), f"{n}.pdf")


def test_dashboard_counts_and_values(pipe, eng):
    seed_all(pipe)
    with eng.connect() as c:
        m = compute_metrics(repo.list_runs(c))
    assert m["total"] == 7 and m["counts"] == {"APPROVED": 2, "REVIEW": 4, "REJECTED": 1} and m["open_review"] == 4
    assert m["submitted_value"]["USD"] == Decimal("1803.60") + Decimal("1570.76") + Decimal("3696.00") + Decimal("1803.60") + Decimal("5832.00")
    assert m["approved_value"] == {"USD": Decimal("1803.60") + Decimal("1570.76")}      # no INR approved, currencies never merged
    assert m["submitted_value"]["INR"] == Decimal("354000.00") and set(m["submitted_value"]) == {"USD", "INR"}


def test_effective_outcome_follows_reviewer_chain(pipe, eng):
    r = pipe.process(sample("03-missing-po"), "03.pdf")
    pipe.revalidate(r.run_id, "P", "ok", {"po_number": "PO-24003"})
    r2 = pipe.process(sample("06-over-budget"), "06.pdf")
    repo.add_review_action(eng, r2.run_id, "REJECTED", "P", "no", "REJECTED_BY_REVIEWER")
    with eng.connect() as c:
        runs = repo.list_runs(c)
        m = compute_metrics(runs)
    assert len(runs) == 3 and m["total"] == 2 and m["runs_total"] == 3
    assert m["counts"] == {"APPROVED": 1, "REVIEW": 0, "REJECTED": 1} and m["open_review"] == 0
    assert {e["outcome"] for e in effective_runs(runs)} == {"APPROVED", "REJECTED"}


def test_empty_database_metrics(eng):
    with eng.connect() as c:
        m = compute_metrics(repo.list_runs(c))
    assert m["total"] == 0 and m["submitted_value"] == {} and m["counts"] == {"APPROVED": 0, "REVIEW": 0, "REJECTED": 0}


def test_search_and_filters(pipe, eng):
    seed_all(pipe)
    with eng.connect() as c:
        runs = repo.list_runs(c)
    assert len(filter_runs(runs, query="inv-10231")) == 1                    # approved original (key stored as typed)
    assert {r["po_number"] for r in filter_runs(runs, query="PO-24005")} == {"PO-24005"}
    assert len(filter_runs(runs, decisions=["REJECTED"])) == 1
    assert all(r["currency"] == "INR" for r in filter_runs(runs, currency="INR")) and len(filter_runs(runs, currency="INR")) == 2
    assert len(filter_runs(runs, vendor="Apex Logistics Partners LLC")) == 1
    assert len(filter_runs(runs, query="kfm-7781", decisions=["REVIEW"])) == 1
    today = date.today()
    assert len(filter_runs(runs, start=today, end=today)) == 7 and filter_runs(runs, start=date(2999, 1, 1)) == []
    assert filter_runs(runs, query="does-not-exist") == []
