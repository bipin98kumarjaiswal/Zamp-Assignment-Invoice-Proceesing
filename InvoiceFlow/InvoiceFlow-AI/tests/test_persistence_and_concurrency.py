import threading
from decimal import Decimal

import pytest
from sqlalchemy import func, select

import app.core.pipeline as pl
from app.config import get_settings, normalize_db_url
from app.core.models import Check, CheckStatus
from app.core.pipeline import InvoicePipeline
from app.db import repo, schema as S
from app.db.engine import init_db, make_engine, reset_db
from tests.conftest import check, fresh_engine, pdf, sample


def test_run_is_persisted_and_reloadable_after_restart(tmp_path):
    e1 = fresh_engine(tmp_path, "p.db"); url = e1.url.render_as_string(hide_password=False)
    r = InvoicePipeline(e1, get_settings()).process(sample("01-clean"), "01.pdf")
    e1.dispose()
    e2 = make_engine(url); init_db(e2)                       # "restart": new engine, init_db must not wipe or re-seed
    with e2.connect() as c:
        back = repo.load_result(c, r.run_id)
        assert back.decision.value == "APPROVED" and back.invoice.invoice_number.value == "INV-10231"
        assert back.checks and [s.n for s in back.stages] == list(range(1, 11))
        assert repo.fetch_document(c, r.run_id) == sample("01-clean")
        assert c.execute(select(func.count()).select_from(S.vendors)).scalar() == 9
        assert c.execute(select(func.count()).select_from(S.purchase_orders)).scalar() == 15
        assert [a["event"] for a in repo.audit_for_run(c, r.run_id)] == ["RUN_PROCESSED"]


def test_approval_commits_po_balance_and_nothing_else_does(pipe, eng):
    pipe.process(sample("03-missing-po"), "03.pdf")
    pipe.process(sample("06-over-budget"), "06.pdf")
    with eng.connect() as c:
        assert repo.get_po(c, "PO-24005")["committed_net"] == 380000
    pipe.process(sample("01-clean"), "01.pdf")
    with eng.connect() as c:
        assert repo.get_po(c, "PO-24001")["remaining"] == 0


def test_reset_restores_seed(pipe, eng):
    pipe.process(sample("01-clean"), "01.pdf")
    reset_db(eng)
    with eng.connect() as c:
        assert repo.count_runs(c) == 0 and repo.get_po(c, "PO-24001")["committed_net"] == 0


def test_db_url_normalisation():
    assert normalize_db_url("postgres://u:p@h:5432/db") == "postgresql+psycopg2://u:p@h:5432/db"
    assert normalize_db_url("postgresql://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert normalize_db_url("sqlite:///x.db") == "sqlite:///x.db"


def test_concurrent_identical_submissions_approve_exactly_one(tmp_path):
    e = fresh_engine(tmp_path, "c.db")
    data, results, barrier = pdf(), [], threading.Barrier(4)

    def work(i):
        p = InvoicePipeline(e, get_settings())
        barrier.wait()
        results.append(p.process(data, f"copy-{i}.pdf"))

    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(r.decision.value for r in results) == ["APPROVED", "REJECTED", "REJECTED", "REJECTED"]
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(S.approved_invoices)).scalar() == 1
        assert repo.get_po(c, "PO-24001")["committed_net"] == Decimal("1670.00")   # committed once, not four times


def test_race_guard_rejects_when_duplicate_check_was_stale(pipe, monkeypatch):
    """Simulate losing the race: the duplicate check saw nothing, but another approval landed before our commit."""
    orion = dict(vendor="Orion Cloud Hosting Inc", po="PO-24009", tax_rate=8, invoice_no="OR-77",
                 lines=[("Reserved compute instance (month)", 1, "980.00")])
    assert pipe.process(pdf(**orion), "first.pdf").decision.value == "APPROVED"
    monkeypatch.setattr(pl, "detect_duplicates", lambda *a, **k: ([Check(id="dup_stub", category="duplicate", name="stub",
                                                                         status=CheckStatus.PASS, detail="stale")], None))
    r = pipe.process(pdf(**orion, invoice_date="2026-11-09"), "second.pdf")
    assert r.decision.value == "REJECTED" and check(r, "commit_race").status.value == "REJECT"
    assert r.stages[-1].n == 10 and r.run_id


def test_race_guard_blocks_joint_overspend_of_a_po(pipe, eng, monkeypatch):
    mk = lambda no, n: pdf(vendor="Brightline Office Solutions Inc", po="PO-24011", tax_rate=7, invoice_no=no,
                           lines=[("Ergonomic office chair", n, "150.00"), ("Standing desk", 2, "200.00")])
    with eng.connect() as c:
        stale = repo.get_po(c, "PO-24011")                   # snapshot before anyone commits
    orig = pl.run_matching
    assert pipe.process(mk("R-1", 10), "r1.pdf").decision.value == "APPROVED"   # 1900 net; remaining now 1100
    def stale_match(inv, conn):
        m = orig(inv, conn); m["po"] = stale; return m
    monkeypatch.setattr(pl, "run_matching", stale_match)
    r = pipe.process(pdf(vendor="Brightline Office Solutions Inc", po="PO-24011", tax_rate=7, invoice_no="R-2",
                         invoice_date="2026-11-04", lines=[("Ergonomic office chair", 10, "150.00"), ("Standing desk", 2, "200.00")]), "r2.pdf")  # sees 3000 remaining, but only 1100 really is
    assert r.decision.value == "REVIEW" and check(r, "commit_race").status.value == "FAIL"
    with eng.connect() as c:
        assert repo.get_po(c, "PO-24011")["committed_net"] == Decimal("2900.00")


def test_nothing_is_saved_as_approved_if_commit_fails(pipe, eng, monkeypatch):
    monkeypatch.setattr(pl, "detect_duplicates", lambda *a, **k: ([], None))
    pipe.process(pdf(), "a.pdf")
    pipe.process(pdf(), "b.pdf")
    with eng.connect() as c:
        assert c.execute(select(func.count()).select_from(S.approved_invoices)).scalar() == 1
