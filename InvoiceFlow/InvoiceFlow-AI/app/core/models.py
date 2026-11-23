"""Pydantic models shared by the engine, the database layer and the UI."""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Decision(str, Enum):
    APPROVED = "APPROVED"
    REVIEW = "REVIEW"
    REJECTED = "REJECTED"


class CheckStatus(str, Enum):
    PASS = "PASS"            # check succeeded
    WARN = "WARN"            # succeeded, but worth noting (e.g. variance inside tolerance)
    FAIL = "FAIL"            # a human must look (-> REVIEW)
    UNCERTAIN = "UNCERTAIN"  # evidence too weak to trust (-> REVIEW)
    REJECT = "REJECT"        # explicit rejection condition (-> REJECTED)
    SKIPPED = "SKIPPED"      # could not be evaluated because an earlier step failed


class Check(BaseModel):
    id: str
    category: Literal["file", "extraction", "data", "po_match", "financial", "duplicate"]
    name: str
    status: CheckStatus
    detail: str
    expected: str | None = None
    actual: str | None = None
    evidence: str | None = None
    next_action: str | None = None


class Field_(BaseModel):
    """One extracted value, with where it came from and how much we trust it."""
    value: str | None = None
    confidence: float = 0.0
    evidence: str | None = None
    source: str = "rules"       # rules | ocr | ai | reviewer
    problem: str | None = None


class LineItem(BaseModel):
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    evidence: str | None = None


class InvoiceData(BaseModel):
    vendor_name: Field_ = Field_()
    vendor_address: Field_ = Field_()
    invoice_number: Field_ = Field_()
    invoice_date: Field_ = Field_()
    due_date: Field_ = Field_()
    po_numbers: list[str] = []          # all distinct PO references found (normalised)
    po_evidence: str | None = None
    po_confidence: float = 0.0
    currency: Field_ = Field_()
    subtotal: Field_ = Field_()
    discount: Field_ = Field_()
    tax_rate: Field_ = Field_()
    tax_amount: Field_ = Field_()
    total: Field_ = Field_()
    line_items: list[LineItem] = []
    notes: list[str] = []


class StageResult(BaseModel):
    n: int
    key: str
    name: str
    status: Literal["success", "warning", "failed", "skipped"]
    summary: str
    started_at: str
    duration_ms: int
    data: dict[str, Any] = {}


class RunResult(BaseModel):
    filename: str
    file_size: int
    doc_hash: str
    page_count: int = 0
    extraction_method: str = "none"
    ocr_confidence: float | None = None
    raw_text: str = ""
    invoice: InvoiceData = InvoiceData()
    vendor_id: str | None = None
    matched_po: str | None = None
    po_snapshot: dict[str, Any] = {}
    calc: dict[str, Any] = {}
    checks: list[Check] = []
    stages: list[StageResult] = []
    warnings: list[str] = []
    errors: list[str] = []
    decision: Decision = Decision.REVIEW
    explanation: str = ""
    summary_line: str = ""
    next_actions: list[str] = []
    duplicate_of_run: int | None = None
    parent_run_id: int | None = None
    run_id: int | None = None
    duration_ms: int = 0
    created_at: str | None = None
    actor: str = "system"
