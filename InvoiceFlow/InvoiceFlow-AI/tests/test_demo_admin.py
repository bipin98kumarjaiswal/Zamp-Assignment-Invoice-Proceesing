"""Checks for safe demo deletion/reset, using an isolated SQLite database."""
from sqlalchemy import func, select

from app.db import repo, schema as S
from app.db.demo_admin import delete_invoice, reset_demo_history
from app.seed_data import PURCHASE_ORDERS
from app.core.utils import to_minor
from decimal import Decimal

from tests.conftest import sample


def test_delete_approved_invoice_reverses_po_balance(pipe, eng):
    first = pipe.process(sample("01-clean"), "01-clean.pdf")
    assert first.decision.value == "APPROVED"
    with eng.connect() as conn:
        before = conn.execute(select(S.purchase_orders.c.committed_net).where(
            S.purchase_orders.c.po_number == first.matched_po
        )).scalar_one()
    assert delete_invoice(eng, first.run_id) == 1
    with eng.connect() as conn:
        assert repo.get_run(conn, first.run_id) is None
        assert not repo.approved_by_hash(conn, first.doc_hash)
        after = conn.execute(select(S.purchase_orders.c.committed_net).where(
            S.purchase_orders.c.po_number == first.matched_po
        )).scalar_one()
    assert after < before
    again = pipe.process(sample("01-clean"), "01-clean.pdf")
    assert again.decision.value == "APPROVED"


def test_reset_preserves_seed_data_and_clears_all_activity(pipe, eng):
    clean = pipe.process(sample("01-clean"), "01-clean.pdf")
    assert clean.decision.value == "APPROVED"
    pipe.process(sample("03-missing-po"), "03-missing-po.pdf")
    assert reset_demo_history(eng) == 2
    with eng.connect() as conn:
        for table in (S.runs, S.documents, S.approved_invoices, S.review_actions, S.audit_log):
            assert conn.execute(select(func.count()).select_from(table)).scalar_one() == 0
        for po, _, _, _, _, original, _, _ in PURCHASE_ORDERS:
            committed = conn.execute(select(S.purchase_orders.c.committed_net).where(
                S.purchase_orders.c.po_number == po
            )).scalar_one()
            assert committed == to_minor(Decimal(original))
    again = pipe.process(sample("01-clean"), "01-clean.pdf")
    assert again.decision.value == "APPROVED"


def test_cannot_delete_missing_invoice(eng):
    import pytest
    with pytest.raises(ValueError, match="not found"):
        delete_invoice(eng, 123456)
