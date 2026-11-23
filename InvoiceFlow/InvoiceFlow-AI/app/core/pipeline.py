"""The 10-stage invoice pipeline. Each stage reports real results through `on_stage` as it completes."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Callable

from app.config import Settings, get_settings
from app.core import ai_extract, decision as dec
from app.core.duplicates import detect_duplicates
from app.core.matching import run_matching
from app.core.models import Check, CheckStatus as C, Decision, Field_, InvoiceData, RunResult, StageResult
from app.core.parser import parse_invoice
from app.core.pdf_extract import extract_pdf, validate_file
from app.core.utils import normalize_po_number, parse_date
from app.core.validation import validate_data, validate_financials
from app.db import repo
from app.db.engine import get_engine

STAGES = [
    (1, "received", "Document received"), (2, "file_validation", "File validation"),
    (3, "extraction", "Text extraction / OCR"), (4, "fields", "Structured field extraction"),
    (5, "data_validation", "Data validation"), (6, "po_matching", "Purchase order matching"),
    (7, "financial", "Financial validation"), (8, "duplicates", "Duplicate detection"),
    (9, "decision", "Decision generation"), (10, "audit", "Audit record saved"),
]
NAME = {n: (k, t) for n, k, t in STAGES}
EDITABLE_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "currency", "po_number")
Callback = Callable[[StageResult], None]


def _status_from(checks: list[Check]) -> str:
    st = {c.status for c in checks}
    if st & {C.FAIL, C.UNCERTAIN, C.REJECT}:
        return "failed"
    return "warning" if C.WARN in st else "success"


class InvoicePipeline:
    def __init__(self, engine=None, settings: Settings | None = None):
        self.engine = engine or get_engine()
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ helpers
    def _stage(self, res, n, status, summary, t0, data=None, on_stage=None) -> StageResult:
        key, title = NAME[n]
        st = StageResult(n=n, key=key, name=title, status=status, summary=summary,
                         started_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                         duration_ms=int((time.perf_counter() - t0) * 1000), data=data or {})
        res.stages.append(st)
        if on_stage and n != 10:
            on_stage(st)
        return st

    def _skip_rest(self, res, first: int, reason: str, on_stage) -> None:
        for n in range(first, 9):
            self._stage(res, n, "skipped", reason, time.perf_counter(), on_stage=on_stage)

    # ------------------------------------------------------------------ main entry points
    def process(self, data: bytes, filename: str, actor: str = "system", on_stage: Callback | None = None,
                _prefilled: RunResult | None = None, _parent: int | None = None,
                _ocr_verified_by: str | None = None) -> RunResult:
        t_all = time.perf_counter()
        s = self.settings
        res = RunResult(filename=filename, file_size=len(data), doc_hash=hashlib.sha256(data).hexdigest(),
                        actor=actor, parent_run_id=_parent)

        # 1 ------------------------------------------------------------ received
        t0 = time.perf_counter()
        self._stage(res, 1, "success", f"{filename} - {len(data):,} bytes - SHA-256 {res.doc_hash[:16]}...", t0,
                    {"filename": filename, "bytes": len(data), "sha256": res.doc_hash}, on_stage)

        # 2 ------------------------------------------------------------ file validation
        t0 = time.perf_counter()
        if _prefilled is None:
            problems = validate_file(data, filename, s)
        else:
            problems = []
        if problems:
            res.checks.append(Check(id="file_valid", category="file", name="File is a readable PDF within limits", status=C.FAIL,
                                    detail=" ".join(problems), next_action="Ask the sender for a valid PDF (max %d MB)." % s.max_file_mb))
            res.errors += problems
            self._stage(res, 2, "failed", " ".join(problems), t0, {"problems": problems}, on_stage)
            self._skip_rest(res, 3, "Skipped: the file failed validation.", on_stage)
            return self._finish(res, data, t_all, on_stage, store_doc=bool(data) and len(data) <= s.max_file_mb * 1024 * 1024)
        res.checks.append(Check(id="file_valid", category="file", name="File is a readable PDF within limits", status=C.PASS,
                                detail=f"PDF header present, {len(data) / 1024:.1f} KB (limit {s.max_file_mb} MB)."))
        self._stage(res, 2, "success", "Valid PDF header, size within limit.", t0, {"size_ok": True}, on_stage)

        # 3 ------------------------------------------------------------ extraction
        t0 = time.perf_counter()
        if _prefilled is not None:
            ex_lines = _prefilled.raw_text.splitlines()
            res.page_count, res.extraction_method, res.ocr_confidence = (_prefilled.page_count, _prefilled.extraction_method,
                                                                         _prefilled.ocr_confidence)
            res.raw_text = _prefilled.raw_text
            self._stage(res, 3, "success", f"Reused text from run #{_parent} ({res.extraction_method}).", t0,
                        {"method": res.extraction_method, "reused_from_run": _parent}, on_stage)
        else:
            ex = extract_pdf(data, s)
            res.page_count, res.extraction_method, res.ocr_confidence = ex.page_count, ex.method, ex.ocr_confidence
            res.raw_text, ex_lines = ex.text, ex.lines
            res.warnings += ex.warnings
            if ex.errors:
                res.errors += ex.errors
                res.checks.append(Check(id="text_extracted", category="extraction", name="Readable text extracted", status=C.FAIL,
                                        detail=" ".join(ex.errors), next_action="Request a clearer or uncorrupted copy of the invoice."))
                self._stage(res, 3, "failed", ex.errors[0], t0, {"errors": ex.errors, "pages": ex.page_count}, on_stage)
                self._skip_rest(res, 4, "Skipped: no readable text.", on_stage)
                return self._finish(res, data, t_all, on_stage)
            msg = {"text": f"Text layer read from {ex.page_count} page(s), {len(ex.lines)} lines.",
                   "ocr": f"Scanned document: OCR on {len(ex.ocr_pages)} page(s), mean word confidence {ex.ocr_confidence}%.",
                   "text+ocr": f"Mixed document: OCR used on page(s) {ex.ocr_pages} (confidence {ex.ocr_confidence}%)."}[ex.method]
            res.checks.append(Check(id="text_extracted", category="extraction", name="Readable text extracted", status=C.PASS,
                                    detail=msg))
            self._stage(res, 3, "warning" if ex.used_ocr or ex.warnings else "success", msg, t0,
                        {"method": ex.method, "pages": ex.page_count, "lines": len(ex.lines), "ocr_confidence": ex.ocr_confidence,
                         "warnings": ex.warnings}, on_stage)

        used_ocr = "ocr" in res.extraction_method

        # 4 ------------------------------------------------------------ field extraction
        t0 = time.perf_counter()
        if _prefilled is not None:
            inv = _prefilled.invoice.model_copy(deep=True)
            summ = f"Fields carried over from run #{_parent}, including reviewer corrections."
        else:
            inv = parse_invoice(ex_lines, "ocr" if used_ocr else "text", res.ocr_confidence)
            notes = ai_extract.enhance_with_ai(res.raw_text, inv, s)
            inv.notes += notes
            found = sum(1 for k in ("vendor_name", "invoice_number", "invoice_date", "due_date", "currency", "subtotal",
                                    "tax_amount", "total") if getattr(inv, k).value) + (1 if inv.po_numbers else 0)
            summ = f"{found} of 9 header/total fields found, {len(inv.line_items)} line item(s)" + (
                "; AI assisted" if notes else "") + "."
        res.invoice = inv
        self._stage(res, 4, "success" if inv.invoice_number.value and inv.total.value else "warning", summ, t0,
                    {"fields": {k: getattr(inv, k).value for k in ("vendor_name", "invoice_number", "invoice_date", "due_date",
                                                                   "currency", "subtotal", "tax_amount", "total")},
                     "po_numbers": inv.po_numbers, "line_items": len(inv.line_items)}, on_stage)

        # 5 ------------------------------------------------------------ data validation
        t0 = time.perf_counter()
        c5 = validate_data(inv, s, used_ocr, res.ocr_confidence, _ocr_verified_by)
        res.checks += c5
        self._stage(res, 5, _status_from(c5), self._count_line(c5), t0, {"checks": len(c5)}, on_stage)

        # 6 ------------------------------------------------------------ PO matching
        t0 = time.perf_counter()
        with self.engine.connect() as conn:
            m = run_matching(inv, conn)
            c6, vendor, po = m["checks"], m["vendor"], m["po"]
            res.vendor_id = vendor["id"] if vendor else None
            res.matched_po = po["po_number"] if po else None
            if po:
                res.po_snapshot = {k: (str(v) if not isinstance(v, (list, str, type(None))) else v) for k, v in po.items() if k != "lines"}
                res.po_snapshot["lines"] = [{k: str(v) for k, v in l.items()} for l in po["lines"]]
            if m["candidates"]:
                res.po_snapshot["suggested_pos"] = m["candidates"]
        res.checks += c6
        self._stage(res, 6, _status_from(c6), self._po_summary(vendor, po, c6), t0,
                    {"vendor": vendor["name"] if vendor else None, "po": res.matched_po,
                     "line_matches": m["line_matches"], "suggested": m["candidates"]}, on_stage)

        # 7 ------------------------------------------------------------ financial validation
        t0 = time.perf_counter()
        c7, calc = validate_financials(inv, po, m["line_matches"], s)
        res.calc = calc
        res.checks += c7
        self._stage(res, 7, _status_from(c7), self._count_line(c7), t0, {"calc": calc}, on_stage)

        # 8 ------------------------------------------------------------ duplicate detection
        t0 = time.perf_counter()
        with self.engine.connect() as conn:
            exclude = repo.lineage_ids(conn, _parent) if _parent else []
            c8, dup_run = detect_duplicates(inv, res.vendor_id, res.doc_hash, conn, exclude)
        res.duplicate_of_run = dup_run
        res.checks += c8
        self._stage(res, 8, _status_from(c8), self._count_line(c8), t0, {"duplicate_of_run": dup_run}, on_stage)

        return self._finish(res, data, t_all, on_stage)

    # ------------------------------------------------------------------ decision + persistence
    def _finish(self, res: RunResult, data: bytes, t_all: float, on_stage, store_doc: bool = True) -> RunResult:
        t0 = time.perf_counter()
        res.decision = dec.decide(res.checks)
        dec.explain(res)
        res.warnings += [f"{c.name}: {c.detail}" for c in res.checks if c.status == C.WARN]
        dec_stage = self._stage(res, 9, {"APPROVED": "success", "REVIEW": "warning", "REJECTED": "failed"}[res.decision.value],
                                f"{res.decision.value}: {res.summary_line}", t0,
                                {"decision": res.decision.value}, on_stage)
        for attempt in range(2):
            t0 = time.perf_counter()
            res.duration_ms = int((time.perf_counter() - t_all) * 1000)
            audit = self._stage(res, 10, "success", "Run, evidence and decision saved to the database.", t0, {}, None)
            cap = res.calc.get("balance_cap_minor")
            try:
                run_id = repo.commit_run(self.engine, res, data if store_doc else None, cap, store_doc)
                break
            except repo.CommitConflict as exc:  # a concurrent approval won: downgrade honestly
                res.stages.remove(audit)
                if exc.kind == "duplicate":
                    res.checks.append(Check(id="commit_race", category="duplicate", name="Concurrent duplicate submission",
                                            status=C.REJECT, detail=exc.detail,
                                            next_action="Do not pay again; the other submission was approved first."))
                else:
                    res.checks.append(Check(id="commit_race", category="financial", name="PO balance consumed concurrently",
                                            status=C.FAIL, detail=exc.detail,
                                            next_action="Re-check the remaining PO balance and re-validate."))
                res.decision = dec.decide(res.checks)
                dec.explain(res)
                dec_stage.status = {"APPROVED": "success", "REVIEW": "warning", "REJECTED": "failed"}[res.decision.value]
                dec_stage.summary = f"{res.decision.value} (revised after concurrent conflict): {res.summary_line}"
        else:  # pragma: no cover
            raise RuntimeError("could not persist run")
        res.run_id = run_id
        with self.engine.connect() as conn:  # read-back verification: the audit record must really exist
            stored = repo.get_run(conn, run_id)
        if not stored or stored["decision"] != res.decision.value:
            raise RuntimeError("Audit record verification failed")
        audit.duration_ms = int((time.perf_counter() - t0) * 1000)
        audit.summary = f"Run #{run_id} saved and verified by read-back."
        if on_stage:
            on_stage(audit)
        return res

    # ------------------------------------------------------------------ reviewer re-validation
    def revalidate(self, parent_run_id: int, reviewer: str, note: str, overrides: dict[str, str] | None = None,
                   ocr_verified: bool = False, on_stage: Callback | None = None) -> RunResult:
        """Reviewer corrects *identity* fields only (never amounts) and the whole deterministic engine runs again.

        A reviewer can therefore never approve by clicking a button: approval only comes out of the same rules.
        """
        overrides = {k: v.strip() for k, v in (overrides or {}).items() if v and v.strip()}
        bad = set(overrides) - set(EDITABLE_FIELDS)
        if bad:
            raise ValueError(f"Fields not editable by reviewers: {sorted(bad)}")
        if not reviewer.strip() or not note.strip():
            raise ValueError("Reviewer name and a justification are required.")
        with self.engine.connect() as conn:
            parent = repo.load_result(conn, parent_run_id)
            content = repo.fetch_document(conn, parent_run_id)
        if parent is None or content is None:
            raise ValueError("The original run or its document is not available.")
        inv = parent.invoice.model_copy(deep=True)
        ev = f"Corrected by {reviewer}: {note}"
        for k, v in overrides.items():
            if k == "po_number":
                po = normalize_po_number(v)
                if not po:
                    raise ValueError("PO number must contain 4-8 digits, e.g. PO-24001.")
                inv.po_numbers, inv.po_confidence, inv.po_evidence = [po], 1.0, ev
            elif k == "invoice_date":
                d, prob = parse_date(v)
                inv.invoice_date = Field_(value=d.isoformat() if d else v, confidence=1.0 if d else 0.0, evidence=ev,
                                          source="reviewer", problem=prob)
            else:
                setattr(inv, k, Field_(value=v.upper() if k == "currency" else v, confidence=1.0, evidence=ev, source="reviewer"))
        pre = parent.model_copy(update={"invoice": inv})
        child = self.process(content, parent.filename, actor=reviewer, on_stage=on_stage, _prefilled=pre,
                             _parent=parent_run_id, _ocr_verified_by=reviewer if ocr_verified else None)
        detail = note + (" | corrections: " + ", ".join(f"{k}={v}" for k, v in overrides.items()) if overrides else "") + (
            " | OCR values verified" if ocr_verified else "")
        repo.add_review_action(self.engine, parent_run_id, "REVALIDATED", reviewer, detail, None, child.run_id)
        return child

    # ------------------------------------------------------------------ summaries
    @staticmethod
    def _count_line(checks: list[Check]) -> str:
        n = lambda *s: sum(1 for c in checks if c.status in s)
        bits = [f"{n(C.PASS)} passed"]
        if n(C.WARN):
            bits.append(f"{n(C.WARN)} noted")
        if n(C.FAIL, C.UNCERTAIN, C.REJECT):
            bits.append(f"{n(C.FAIL, C.UNCERTAIN, C.REJECT)} failed")
        if n(C.SKIPPED):
            bits.append(f"{n(C.SKIPPED)} skipped")
        return ", ".join(bits)

    @staticmethod
    def _po_summary(vendor, po, checks) -> str:
        who = vendor["name"] if vendor else "vendor not identified"
        return f"{who}; " + (f"matched {po['po_number']}" if po else "no PO matched") + f" ({InvoicePipeline._count_line(checks)})"
