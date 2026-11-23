"""Password-protected demo data cleanup; never expose without authorization.

Both operations use one transaction and preserve seeded master vendor/PO data.
"""
from decimal import Decimal
import json

from sqlalchemy import delete, select, update

from app.core.utils import to_minor
from app.db import schema as S
from app.seed_data import PURCHASE_ORDERS


def _descendants(conn, root_id: int) -> set[int]:
    existing = conn.execute(select(S.runs.c.id).where(S.runs.c.id == root_id)).scalar_one_or_none()
    if existing is None:
        raise ValueError("Invoice run not found.")
    ids = {root_id}
    while True:
        children = set(conn.execute(
            select(S.runs.c.id).where(S.runs.c.parent_run_id.in_(ids))
        ).scalars())
        if children.issubset(ids):
            return ids
        ids.update(children)


def delete_invoice(engine, run_id: int) -> int:
    """Delete the selected run's entire review lineage, restoring approved PO commitments.

    Deleting a child re-check means deleting its root too, to avoid broken audit lineage.
    """
    with engine.begin() as conn:
        root = run_id
        while True:
            parent = conn.execute(
                select(S.runs.c.parent_run_id).where(S.runs.c.id == root)
            ).scalar_one_or_none()
            if not parent:
                break
            root = parent
        ids = _descendants(conn, root)
        approved = conn.execute(
            select(S.runs.c.po_number, S.runs.c.calc_json).where(
                S.runs.c.id.in_(ids),
                S.runs.c.decision == "APPROVED",
            )
        ).all()
        adjustments: dict[str, int] = {}
        for po, calc_json in approved:
            calc = json.loads(calc_json or "{}")
            if not po or calc.get("net_amount") is None:
                raise ValueError("Cannot reverse this approval's PO balance safely.")
            adjustments[po] = adjustments.get(po, 0) + to_minor(
                Decimal(str(calc["net_amount"]))
            )
        for po, value in adjustments.items():
            result = conn.execute(
                update(S.purchase_orders)
                .where(
                    S.purchase_orders.c.po_number == po,
                    S.purchase_orders.c.committed_net >= value,
                )
                .values(committed_net=S.purchase_orders.c.committed_net - value)
            )
            if result.rowcount != 1:
                raise ValueError("PO balance no longer matches approved records; deletion cancelled.")
        for table in (S.documents, S.approved_invoices, S.review_actions, S.audit_log):
            conn.execute(delete(table).where(table.c.run_id.in_(ids)))
        # Remove descendants before parents; the model's foreign key is restrictive.
        pending = ids.copy()
        while pending:
            parents = set(conn.execute(
                select(S.runs.c.parent_run_id).where(
                    S.runs.c.id.in_(pending),
                    S.runs.c.parent_run_id.is_not(None),
                )
            ).scalars())
            leaves = pending - parents
            if not leaves:
                raise ValueError("Invalid review lineage; deletion cancelled.")
            conn.execute(delete(S.runs).where(S.runs.c.id.in_(leaves)))
            pending -= leaves
    return len(ids)


def reset_demo_history(engine) -> int:
    """Clear invoice activity while retaining vendors, POs and PO line items."""
    baseline = {po: to_minor(Decimal(committed)) for
                po, _vendor, _cur, _tax, _state, committed, _desc, _lines
                in PURCHASE_ORDERS}
    with engine.begin() as conn:
        count = conn.execute(select(S.runs.c.id)).all()
        # Reference seed data must exist; do not silently reset unrelated POs.
        existing = dict(conn.execute(select(
            S.purchase_orders.c.po_number, S.purchase_orders.c.committed_net
        )).all())
        if set(existing) != set(baseline):
            raise ValueError("Purchase orders differ from demo seed data; reset cancelled.")
        for table in (S.documents, S.approved_invoices, S.review_actions, S.audit_log):
            conn.execute(delete(table))
        # PostgreSQL and SQLite both accept deleting descendants via NULL before delete.
        conn.execute(update(S.runs).values(parent_run_id=None))
        conn.execute(delete(S.runs))
        for po, committed in baseline.items():
            conn.execute(update(S.purchase_orders)
                         .where(S.purchase_orders.c.po_number == po)
                         .values(committed_net=committed))
    return len(count)
