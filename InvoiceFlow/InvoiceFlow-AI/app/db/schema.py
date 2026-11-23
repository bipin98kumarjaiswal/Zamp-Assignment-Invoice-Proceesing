"""Portable schema (SQLite + PostgreSQL). Money is stored as integer minor units (cents/paise)."""
from __future__ import annotations

from sqlalchemy import (BigInteger, Column, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, MetaData,
                        String, Table, Text, UniqueConstraint)

metadata = MetaData()

vendors = Table(
    "vendors", metadata,
    Column("id", String(20), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("norm_name", String(200), nullable=False, index=True),
    Column("status", String(20), nullable=False),   # ACTIVE | INACTIVE | BLOCKED
    Column("country", String(5)),
    Column("tax_id", String(50)),
    Column("address", String(300)),
)

purchase_orders = Table(
    "purchase_orders", metadata,
    Column("po_number", String(30), primary_key=True),
    Column("vendor_id", String(20), ForeignKey("vendors.id"), nullable=False, index=True),
    Column("currency", String(3), nullable=False),
    Column("amount_net", BigInteger, nullable=False),
    Column("committed_net", BigInteger, nullable=False, default=0),
    Column("tax_rate", String(10), nullable=False, default="0"),
    Column("status", String(20), nullable=False),    # OPEN | PARTIALLY_BILLED | CLOSED | CANCELLED | ON_HOLD
    Column("description", String(300)),
)

po_lines = Table(
    "po_lines", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("po_number", String(30), ForeignKey("purchase_orders.po_number"), nullable=False, index=True),
    Column("line_no", Integer, nullable=False),
    Column("description", String(300), nullable=False),
    Column("quantity", String(20), nullable=False),
    Column("unit_price", String(20), nullable=False),
)

runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("parent_run_id", Integer, ForeignKey("runs.id"), nullable=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("actor", String(100), nullable=False, default="system"),
    Column("filename", String(300), nullable=False),
    Column("file_size", Integer, nullable=False),
    Column("doc_hash", String(64), nullable=False, index=True),
    Column("page_count", Integer, default=0),
    Column("extraction_method", String(40)),
    Column("ocr_confidence", Float),
    Column("vendor_name", String(300)),
    Column("vendor_id", String(20)),
    Column("invoice_number", String(100)),
    Column("invoice_number_norm", String(100), index=True),
    Column("invoice_date", String(20)),
    Column("due_date", String(20)),
    Column("po_number", String(30), index=True),
    Column("currency", String(3)),
    Column("subtotal_minor", BigInteger),
    Column("tax_minor", BigInteger),
    Column("total_minor", BigInteger),
    Column("decision", String(12), nullable=False, index=True),
    Column("review_state", String(30), nullable=False, default="NONE"),  # NONE|OPEN|INFO_REQUESTED|RESOLVED|REJECTED_BY_REVIEWER
    Column("summary_line", Text),
    Column("explanation", Text),
    Column("duration_ms", Integer, default=0),
    Column("fields_json", Text),
    Column("checks_json", Text),
    Column("stages_json", Text),
    Column("po_json", Text),
    Column("calc_json", Text),
    Column("messages_json", Text),   # warnings, errors, next actions
    Column("raw_text", Text),
)

documents = Table(
    "documents", metadata,
    Column("run_id", Integer, ForeignKey("runs.id"), primary_key=True),
    Column("content", LargeBinary, nullable=False),
)

# Only APPROVED invoices appear here. The unique constraints are the race-condition guard:
# two concurrent submissions of the same invoice cannot both be approved.
approved_invoices = Table(
    "approved_invoices", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("runs.id"), nullable=False, unique=True),
    Column("vendor_id", String(20), nullable=False),
    Column("invoice_number_norm", String(100), nullable=False),
    Column("doc_hash", String(64), nullable=False),
    Column("total_minor", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("vendor_id", "invoice_number_norm", name="uq_approved_vendor_invoice"),
    UniqueConstraint("doc_hash", name="uq_approved_doc_hash"),
)

review_actions = Table(
    "review_actions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("runs.id"), nullable=False, index=True),
    Column("action", String(40), nullable=False),
    Column("actor", String(100), nullable=False),
    Column("note", Text),
    Column("child_run_id", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

audit_log = Table(
    "audit_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, index=True),
    Column("event", String(60), nullable=False),
    Column("actor", String(100), nullable=False),
    Column("detail", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index("ix_runs_vendor_total", runs.c.vendor_id, runs.c.total_minor)
