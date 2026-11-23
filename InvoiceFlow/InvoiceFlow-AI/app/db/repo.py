"""All SQL lives here. Read helpers take a connection; write helpers take an engine and are transactional."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.core.models import RunResult
from app.core.utils import from_minor, to_minor
from app.db import schema as S


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


# ------------------------------------------------------------------ master data (reads)
def all_vendors(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(select(S.vendors).order_by(S.vendors.c.id)).mappings()]


def get_vendor(conn, vendor_id: str) -> dict | None:
    r = conn.execute(select(S.vendors).where(S.vendors.c.id == vendor_id)).mappings().first()
    return dict(r) if r else None


def _po_dict(conn, row, with_lines=True) -> dict:
    d = dict(row)
    d["amount_net"] = from_minor(d["amount_net"])
    d["committed_net"] = from_minor(d["committed_net"])
    d["remaining"] = d["amount_net"] - d["committed_net"]
    d["tax_rate"] = Decimal(d["tax_rate"])
    if with_lines:
        lines = conn.execute(select(S.po_lines).where(S.po_lines.c.po_number == d["po_number"])
                             .order_by(S.po_lines.c.line_no)).mappings().all()
        d["lines"] = [{"line_no": l["line_no"], "description": l["description"],
                       "quantity": Decimal(l["quantity"]), "unit_price": Decimal(l["unit_price"]),
                       "amount": Decimal(l["quantity"]) * Decimal(l["unit_price"])} for l in lines]
    return d


def get_po(conn, po_number: str) -> dict | None:
    r = conn.execute(select(S.purchase_orders).where(S.purchase_orders.c.po_number == po_number)).mappings().first()
    return _po_dict(conn, r) if r else None


def pos_for_vendor(conn, vendor_id: str) -> list[dict]:
    rows = conn.execute(select(S.purchase_orders).where(S.purchase_orders.c.vendor_id == vendor_id)
                        .order_by(S.purchase_orders.c.po_number)).mappings().all()
    return [_po_dict(conn, r) for r in rows]


def list_purchase_orders(conn) -> list[dict]:
    rows = conn.execute(
        select(S.purchase_orders, S.vendors.c.name.label("vendor_name"), S.vendors.c.status.label("vendor_status"))
        .join(S.vendors, S.vendors.c.id == S.purchase_orders.c.vendor_id)
        .order_by(S.purchase_orders.c.po_number)).mappings().all()
    return [_po_dict(conn, r, with_lines=False) for r in rows]


# ------------------------------------------------------------------ duplicate look-ups
def approved_by_key(conn, vendor_id: str, inv_norm: str) -> dict | None:
    r = conn.execute(select(S.approved_invoices).where(and_(
        S.approved_invoices.c.vendor_id == vendor_id,
        S.approved_invoices.c.invoice_number_norm == inv_norm))).mappings().first()
    return dict(r) if r else None


def approved_by_hash(conn, doc_hash: str) -> dict | None:
    r = conn.execute(select(S.approved_invoices).where(S.approved_invoices.c.doc_hash == doc_hash)).mappings().first()
    return dict(r) if r else None


def approved_by_amount(conn, vendor_id: str, total_minor: int, invoice_date: str | None) -> list[dict]:
    q = (select(S.approved_invoices.c.run_id, S.runs.c.invoice_number, S.runs.c.invoice_date)
         .join(S.runs, S.runs.c.id == S.approved_invoices.c.run_id)
         .where(and_(S.approved_invoices.c.vendor_id == vendor_id,
                     S.approved_invoices.c.total_minor == total_minor,
                     S.runs.c.invoice_date == invoice_date)))
    return [dict(r) for r in conn.execute(q).mappings()]


def prior_runs_by_hash(conn, doc_hash: str, exclude: list[int]) -> list[dict]:
    q = select(S.runs.c.id, S.runs.c.decision, S.runs.c.created_at).where(S.runs.c.doc_hash == doc_hash)
    if exclude:
        q = q.where(S.runs.c.id.notin_(exclude))
    return [dict(r) for r in conn.execute(q.order_by(S.runs.c.id)).mappings()]


def prior_runs_by_key(conn, vendor_key: str, inv_norm: str, exclude: list[int]) -> list[dict]:
    q = select(S.runs.c.id, S.runs.c.decision, S.runs.c.review_state).where(and_(
        S.runs.c.invoice_number_norm == inv_norm,
        (S.runs.c.vendor_id == vendor_key) if not vendor_key.startswith("NAME:")
        else S.runs.c.vendor_name.ilike(vendor_key[5:] + "%")))
    if exclude:
        q = q.where(S.runs.c.id.notin_(exclude))
    return [dict(r) for r in conn.execute(q.order_by(S.runs.c.id)).mappings()]


# ------------------------------------------------------------------ persistence of a run
class CommitConflict(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind, self.detail = kind, detail


def commit_run(engine, res: RunResult, content: bytes | None, cap_minor: int | None, store_doc: bool = True) -> int:
    """Insert the run (+document, +approval, +PO commitment) in ONE transaction.

    For APPROVED results this is where races are decided:
      * the UNIQUE constraints on approved_invoices stop two concurrent approvals of the same invoice/file;
      * the conditional UPDATE on purchase_orders stops two invoices from jointly over-spending a PO.
    Raises CommitConflict (and rolls back everything) if either guard trips.
    """
    inv = res.invoice
    total = _dec(inv.total.value)
    row = dict(
        parent_run_id=res.parent_run_id, created_at=utcnow(), actor=res.actor, filename=res.filename,
        file_size=res.file_size, doc_hash=res.doc_hash, page_count=res.page_count,
        extraction_method=res.extraction_method, ocr_confidence=res.ocr_confidence,
        vendor_name=inv.vendor_name.value, vendor_id=res.vendor_id,
        invoice_number=inv.invoice_number.value, invoice_number_norm=_norm_inv(inv.invoice_number.value),
        invoice_date=inv.invoice_date.value, due_date=inv.due_date.value, po_number=res.matched_po,
        currency=inv.currency.value, subtotal_minor=to_minor(_dec(inv.subtotal.value)),
        tax_minor=to_minor(_dec(inv.tax_amount.value)), total_minor=to_minor(total),
        decision=res.decision.value, review_state="OPEN" if res.decision.value == "REVIEW" else "NONE",
        summary_line=res.summary_line, explanation=res.explanation, duration_ms=res.duration_ms,
        fields_json=inv.model_dump_json(), checks_json=_dumps([c.model_dump(mode="json") for c in res.checks]),
        stages_json=_dumps([s.model_dump(mode="json") for s in res.stages]),
        po_json=_dumps(res.po_snapshot), calc_json=_dumps(res.calc),
        messages_json=_dumps({"warnings": res.warnings, "errors": res.errors, "next_actions": res.next_actions,
                              "duplicate_of_run": res.duplicate_of_run, "vendor_id": res.vendor_id}),
        raw_text=res.raw_text)
    try:
        with engine.begin() as conn:
            run_id = conn.execute(insert(S.runs).values(**row)).inserted_primary_key[0]
            if store_doc and content is not None:
                conn.execute(insert(S.documents).values(run_id=run_id, content=content))
            if res.decision.value == "APPROVED":
                vid, norm = res.vendor_id, row["invoice_number_norm"]
                conn.execute(insert(S.approved_invoices).values(
                    run_id=run_id, vendor_id=vid, invoice_number_norm=norm, doc_hash=res.doc_hash,
                    total_minor=row["total_minor"], currency=row["currency"], created_at=utcnow()))
                net = to_minor(Decimal(str(res.calc["net_amount"])))
                n = conn.execute(update(S.purchase_orders).where(and_(
                    S.purchase_orders.c.po_number == res.matched_po,
                    S.purchase_orders.c.committed_net + net <= cap_minor))
                    .values(committed_net=S.purchase_orders.c.committed_net + net)).rowcount
                if n != 1:
                    raise CommitConflict("balance", "PO balance was consumed by another invoice while this one was processing.")
            conn.execute(insert(S.audit_log).values(
                run_id=run_id, event="RUN_PROCESSED", actor=res.actor, created_at=utcnow(),
                detail=_dumps({"decision": res.decision.value, "file": res.filename, "po": res.matched_po,
                               "parent_run_id": res.parent_run_id})))
            if res.parent_run_id:
                conn.execute(update(S.runs).where(S.runs.c.id == res.parent_run_id).values(review_state="RESOLVED"))
        return run_id
    except IntegrityError as exc:
        raise CommitConflict("duplicate", f"Another submission of this invoice was approved at the same moment ({type(exc.orig).__name__}).") from exc


def _dec(v) -> Decimal | None:
    try:
        return None if v in (None, "") else Decimal(str(v))
    except Exception:
        return None


def _norm_inv(v):
    from app.core.utils import normalize_invoice_number
    return normalize_invoice_number(v)


# ------------------------------------------------------------------ reading runs
def _row_to_run(row) -> dict:
    d = dict(row)
    for k in ("subtotal_minor", "tax_minor", "total_minor"):
        d[k.replace("_minor", "")] = from_minor(d.get(k))
    return d


RUN_LIST_COLS = [S.runs.c.id, S.runs.c.parent_run_id, S.runs.c.created_at, S.runs.c.filename, S.runs.c.vendor_name,
                 S.runs.c.invoice_number, S.runs.c.invoice_date, S.runs.c.po_number, S.runs.c.currency,
                 S.runs.c.total_minor, S.runs.c.decision, S.runs.c.review_state, S.runs.c.summary_line,
                 S.runs.c.extraction_method, S.runs.c.duration_ms, S.runs.c.actor]


def list_runs(conn) -> list[dict]:
    rows = conn.execute(select(*RUN_LIST_COLS).order_by(S.runs.c.id.desc())).mappings().all()
    return [_row_to_run(r) for r in rows]


def get_run(conn, run_id: int) -> dict | None:
    r = conn.execute(select(S.runs).where(S.runs.c.id == run_id)).mappings().first()
    return _row_to_run(r) if r else None


def load_result(conn, run_id: int) -> RunResult | None:
    r = get_run(conn, run_id)
    if not r:
        return None
    msgs = json.loads(r["messages_json"] or "{}")
    created = r["created_at"]
    return RunResult.model_validate({
        "filename": r["filename"], "file_size": r["file_size"], "doc_hash": r["doc_hash"],
        "page_count": r["page_count"] or 0, "extraction_method": r["extraction_method"] or "none",
        "ocr_confidence": r["ocr_confidence"], "raw_text": r["raw_text"] or "",
        "invoice": json.loads(r["fields_json"]), "vendor_id": r["vendor_id"], "matched_po": r["po_number"],
        "po_snapshot": json.loads(r["po_json"] or "{}"), "calc": json.loads(r["calc_json"] or "{}"),
        "checks": json.loads(r["checks_json"] or "[]"), "stages": json.loads(r["stages_json"] or "[]"),
        "warnings": msgs.get("warnings", []), "errors": msgs.get("errors", []),
        "decision": r["decision"], "explanation": r["explanation"] or "", "summary_line": r["summary_line"] or "",
        "next_actions": msgs.get("next_actions", []), "duplicate_of_run": msgs.get("duplicate_of_run"),
        "parent_run_id": r["parent_run_id"], "run_id": r["id"], "duration_ms": r["duration_ms"] or 0,
        "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created), "actor": r["actor"],
    })


def fetch_document(conn, run_id: int) -> bytes | None:
    r = conn.execute(select(S.documents.c.content).where(S.documents.c.run_id == run_id)).first()
    return bytes(r[0]) if r else None


def root_run_id(conn, run_id: int) -> int:
    seen = set()
    while run_id not in seen:
        seen.add(run_id)
        p = conn.execute(select(S.runs.c.parent_run_id).where(S.runs.c.id == run_id)).scalar()
        if not p:
            break
        run_id = p
    return run_id


def lineage_ids(conn, run_id: int) -> list[int]:
    """The run itself plus all its ancestors (used so a re-validation is not flagged as its own duplicate)."""
    ids, cur = [], run_id
    while cur and cur not in ids:
        ids.append(cur)
        cur = conn.execute(select(S.runs.c.parent_run_id).where(S.runs.c.id == cur)).scalar()
    return ids


# ------------------------------------------------------------------ review actions & audit
def add_review_action(engine, run_id: int, action: str, actor: str, note: str,
                      new_state: str | None = None, child_run_id: int | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(insert(S.review_actions).values(
            run_id=run_id, action=action, actor=actor, note=note, child_run_id=child_run_id, created_at=utcnow()))
        if new_state:
            conn.execute(update(S.runs).where(S.runs.c.id == run_id).values(review_state=new_state))
        conn.execute(insert(S.audit_log).values(
            run_id=run_id, event=f"REVIEW_{action}", actor=actor, created_at=utcnow(),
            detail=_dumps({"note": note, "child_run_id": child_run_id, "new_state": new_state})))


def list_review_actions(conn, run_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(select(S.review_actions).where(S.review_actions.c.run_id == run_id)
                                          .order_by(S.review_actions.c.id)).mappings()]


def audit_for_run(conn, run_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(select(S.audit_log).where(S.audit_log.c.run_id == run_id)
                                          .order_by(S.audit_log.c.id)).mappings()]


def count_runs(conn) -> int:
    return conn.execute(select(func.count()).select_from(S.runs)).scalar() or 0
