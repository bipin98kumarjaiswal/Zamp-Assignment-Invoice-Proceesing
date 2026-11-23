"""Rule-based invoice field parser. Every value carries evidence (the source line) and a confidence.

Principle: never invent a value. If a field cannot be found it stays None with confidence 0.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.core.models import Field_, InvoiceData, LineItem
from app.core.utils import (DATE_TOKEN, SUPPORTED_CURRENCIES, SYMBOL_TO_CURRENCY, normalize_po_number, parse_date,
                            parse_money, q2)

GENERIC_HEADER = re.compile(r"^(tax\s+)?invoice\b|^bill(ed)?\s+to\b|^page\b|^original\b|^copy\b|^\W*$", re.I)
AMOUNT_TOKEN = re.compile(r"(?<![\w.])[-(]?\s?[$€₹£]?\s?\d[\d,]*(?:\.\d{1,2})?\)?(?![\w%]|\s?%)")
ROW = re.compile(
    r"^(?:\d{1,2}\s+(?=[A-Za-z]))?(?P<desc>[A-Za-z].*?)\s+(?P<qty>\d+(?:\.\d+)?)\s+"
    r"[$€₹£]?\s?(?P<unit>\d[\d,]*\.\d{2,4})\s+[$€₹£]?\s?(?P<amt>\d[\d,]*\.\d{2})\s*$")
NON_ITEM = re.compile(r"\b(sub\s*-?\s*total|total|tax|gst|vat|discount|balance|amount due)\b", re.I)

TOTAL_TIER1 = re.compile(
    r"^(grand\s+total|total\s+due|amount\s+due|balance\s+due|total\s+payable|total\s+amount|invoice\s+total)\b", re.I)
TOTAL_TIER2 = re.compile(r"^total\b(?!\s+tax)", re.I)
SUBTOTAL = re.compile(r"^(sub\s*-?\s*total|net\s+amount|net\s+total|taxable\s+(value|amount))\b", re.I)
DISCOUNT = re.compile(r"^(less:?\s*)?discount\b", re.I)
TAX = re.compile(r"^(tax|gst|igst|cgst|sgst|vat|sales\s+tax)\b(?!\s*(id|no\b|number|reg|registration|#|:?\s*[A-Z0-9-]*\d{6,}))", re.I)
PO_RE = re.compile(
    r"\b(?:P\.?O\.?|purchase\s+order)(?:\s*(?:no\.?|number|num|#|ref\.?|reference))?\s*[:#\-]?\s*(?:PO)?[-\s]?(\d{4,8})\b",
    re.I)


def _f(value, conf, evidence, scale, source="rules", problem=None) -> Field_:
    return Field_(value=value, confidence=round(conf * scale, 3) if value else 0.0, evidence=evidence,
                  source=source, problem=problem)


DECIMAL_AMOUNT = re.compile(r"(?<![\w.])[-(]?\s?[$€₹£]?\s?\d[\d,]*\.\d{2}\)?(?![\w%])")


def _last_amount(line: str) -> str | None:
    """Prefer the last token with cents (1,234.56); fall back to any trailing number."""
    toks = DECIMAL_AMOUNT.findall(line) or AMOUNT_TOKEN.findall(line)
    return toks[-1].strip() if toks else None


def parse_invoice(lines: list[str], method: str = "text", ocr_confidence: float | None = None) -> InvoiceData:
    scale = 1.0 if method == "text" else min(1.0, (ocr_confidence or 0) / 100)
    src = "rules" if method == "text" else "ocr"
    inv = InvoiceData()

    # ---- vendor name & address -------------------------------------------------------------------------
    for i, line in enumerate(lines):
        m = re.match(r"^(?:from|vendor|supplier|bill\s*from|sold\s*by|issued\s*by)\s*[:\-]\s*(.+)$", line, re.I)
        if m:
            inv.vendor_name = _f(m[1].strip(), 0.95, line, scale, src)
            break
    else:
        for i, line in enumerate(lines[:8]):
            seg = next((s.strip() for s in re.split(r"\s{2,}", line) if s.strip() and not GENERIC_HEADER.match(s.strip())), None)
            if seg and len(seg) > 3 and not re.search(r"\d{3,}", seg):
                inv.vendor_name = _f(seg, 0.8, line, scale, src)
                addr = []
                for nxt in lines[i + 1:i + 3]:
                    if re.search(r"\b(invoice|bill\s*to|date|po)\b", nxt, re.I):
                        break
                    addr.append(nxt)
                if addr:
                    inv.vendor_address = _f(", ".join(addr), 0.6, " | ".join(addr), scale, src)
                break

    # ---- invoice number --------------------------------------------------------------------------------
    inv_no = re.compile(r"\b(?:(?:tax\s+)?invoice|bill)\s*(?:no\.?|number|num|#)\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/._]{1,})", re.I)
    for line in lines:
        m = inv_no.search(line)
        if m:
            inv.invoice_number = _f(m[1].rstrip(".,"), 0.95, line, scale, src)
            break

    # ---- dates -------------------------------------------------------------------------------------------
    def find_date(label_re: str, conf: float) -> Field_:
        pat = re.compile(label_re + r"\s*[:\-]?\s*(" + DATE_TOKEN + ")", re.I)
        for line in lines:
            m = pat.search(line)
            if m:
                raw = m[1].strip()
                d, problem = parse_date(raw)
                return _f(d.isoformat() if d else raw, conf, line, scale, src,
                          problem=problem) if d else Field_(value=raw, confidence=0.0, evidence=line,
                                                            source=src, problem=problem)
        return Field_()

    inv.invoice_date = find_date(r"\b(?:invoice\s+date|date\s+of\s+issue|issue\s+date|invoice\s+dated|(?<!due )date)", 0.95)
    inv.due_date = find_date(r"\b(?:due\s+date|payment\s+due(?:\s+date)?|due\s+on|pay\s+by)", 0.9)

    # ---- PO references -----------------------------------------------------------------------------------
    found, ev = [], []
    for line in lines:
        for m in PO_RE.finditer(line):
            po = normalize_po_number(m[1])
            if po and po not in found:
                found.append(po)
                ev.append(line)
    inv.po_numbers = found
    inv.po_evidence = " | ".join(ev) if ev else None
    inv.po_confidence = round(0.95 * scale, 3) if found else 0.0

    # ---- currency ----------------------------------------------------------------------------------------
    cur_label = re.compile(r"\bcurrency\s*[:\-]?\s*([A-Za-z]{3})\b", re.I)
    for line in lines:
        m = cur_label.search(line)
        if m:
            code = m[1].upper()
            inv.currency = _f(code, 0.97, line, scale, src,
                              problem=None if code in SUPPORTED_CURRENCIES else f"'{code}' is not a supported currency code")
            break
    if not inv.currency.value:
        for line in lines:
            if re.match(r"^(grand\s+total|total|amount\s+due|total\s+due)", line, re.I):
                m = re.search(r"\b(USD|EUR|GBP|INR|CAD|AUD|SGD|JPY|CHF|AED)\b", line)
                if m:
                    inv.currency = _f(m[1], 0.9, line, scale, src)
                    break
    if not inv.currency.value:
        text = "\n".join(lines)
        syms = {SYMBOL_TO_CURRENCY[s] for s in ("$", "€", "₹", "£") if s in text}
        if "Rs." in text or re.search(r"\bRs\b", text):
            syms.add("INR")
        if len(syms) == 1:
            sym = next(iter(syms))
            # '$' alone is ambiguous (USD/CAD/AUD...), so confidence stays below the review threshold
            inv.currency = _f(sym, 0.75 if sym == "USD" else 0.85, "currency symbol in document", scale, src)

    # ---- totals ------------------------------------------------------------------------------------------
    def amount_field(matches: list[tuple[str, str]], conf: float) -> Field_:
        if not matches:
            return Field_()
        vals = {parse_money(a) for a, _ in matches if parse_money(a) is not None}
        raw, line = matches[-1]
        v = parse_money(raw)
        if v is None:
            return Field_(value=None, confidence=0.0, evidence=line, source=src, problem=f"could not parse '{raw}'")
        if len(vals) > 1:
            return _f(str(q2(v)), 0.5, line, scale, src, problem="conflicting values found: " + ", ".join(sorted(str(q2(x)) for x in vals)))
        return _f(str(q2(abs(v))), conf, line, scale, src)

    sub, disc, tax, tot1, tot2 = [], [], [], [], []
    tax_rate = None
    for line in lines:
        s = line.strip()
        amt = _last_amount(s)
        if amt is None:
            continue
        if SUBTOTAL.match(s):
            sub.append((amt, s))
        elif DISCOUNT.match(s):
            disc.append((amt, s))
        elif TAX.match(s):
            tax.append((amt, s))
            r = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
            if r and tax_rate is None:
                tax_rate = (r[1], s)
        elif TOTAL_TIER1.match(s):
            tot1.append((amt, s))
        elif TOTAL_TIER2.match(s):
            tot2.append((amt, s))
    inv.subtotal = amount_field(sub, 0.93)
    inv.discount = amount_field(disc, 0.9)
    inv.tax_amount = amount_field(tax, 0.93)
    inv.total = amount_field(tot1 or tot2, 0.95 if tot1 else 0.85)
    if tax_rate:
        inv.tax_rate = _f(tax_rate[0], 0.93, tax_rate[1], scale, src)

    # ---- line items --------------------------------------------------------------------------------------
    for line in lines:
        m = ROW.match(line.strip())
        if not m or NON_ITEM.search(m["desc"]):
            continue
        inv.line_items.append(LineItem(
            description=m["desc"].strip(), quantity=Decimal(m["qty"]), unit_price=parse_money(m["unit"]),
            amount=parse_money(m["amt"]), evidence=line.strip()))
    if not inv.line_items:
        inv.notes.append("No itemised line items could be recognised.")
    return inv
