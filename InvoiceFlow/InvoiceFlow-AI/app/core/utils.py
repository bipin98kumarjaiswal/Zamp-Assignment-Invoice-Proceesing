"""Pure helper functions: money, dates, normalisation. No I/O."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from difflib import SequenceMatcher

CENT = Decimal("0.01")
SUPPORTED_CURRENCIES = {
    "USD", "EUR", "GBP", "INR", "CAD", "AUD", "SGD", "JPY", "CHF", "AED", "CNY", "SEK", "NZD", "ZAR",
}
SYMBOL_TO_CURRENCY = {"$": "USD", "€": "EUR", "₹": "INR", "£": "GBP", "Rs.": "INR", "Rs": "INR"}
LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "pvt", "private", "gmbh", "corp", "corporation",
    "co", "company", "llp", "plc", "sa", "ag", "pty",
}


def q2(value: Decimal) -> Decimal:
    """Round to 2 dp, half-up. This is the project's single rounding rule."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_minor(value: Decimal | None) -> int | None:
    return None if value is None else int((q2(value) * 100).to_integral_value())


def from_minor(value: int | None) -> Decimal | None:
    return None if value is None else (Decimal(int(value)) / 100).quantize(CENT)


def parse_money(raw: str | None) -> Decimal | None:
    """Parse '1,234.56', '1.234,56', '1,50,000.00', '$12.50', '(50.00)' -> Decimal. None if not parseable."""
    if raw is None:
        return None
    s = str(raw).strip()
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = re.sub(r"[^\d,\.]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):  # European: 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        tail = s.rsplit(",", 1)[1]
        s = s.replace(",", ".") if len(tail) in (1, 2) and s.count(",") == 1 else s.replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_date(raw: str | None) -> tuple[date | None, str | None]:
    """Return (date, problem). problem is None on success, else a human-readable reason.

    Numeric day/month dates where both parts are <= 12 and differ (e.g. 03/04/2026) are *ambiguous* and are
    never guessed.
    """
    if not raw:
        return None, "empty"
    s = raw.strip().rstrip(".,")
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]), raw)
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
        if a > 12 and b <= 12:
            return _mk(y, b, a, raw)
        if b > 12 and a <= 12:
            return _mk(y, a, b, raw)
        if a == b:
            return _mk(y, a, b, raw)
        if a <= 12 and b <= 12:
            return None, f"'{raw}' is ambiguous (day/month order cannot be determined)"
        return None, f"'{raw}' is not a valid date"
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
    if m and m[2][:3].lower() in _MONTHS:
        return _mk(int(m[3]), _MONTHS[m[2][:3].lower()], int(m[1]), raw)
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m[1][:3].lower() in _MONTHS:
        return _mk(int(m[3]), _MONTHS[m[1][:3].lower()], int(m[2]), raw)
    return None, f"'{raw}' is not a recognised date format"


def _mk(y: int, mo: int, d: int, raw: str) -> tuple[date | None, str | None]:
    try:
        return date(y, mo, d), None
    except ValueError:
        return None, f"'{raw}' is not a real calendar date"


DATE_TOKEN = (
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"
)


def normalize_invoice_number(raw: str | None) -> str | None:
    """'INV-00231', 'inv 231', 'Inv#231' -> 'INV231'. Used for duplicate detection."""
    if not raw:
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    if not s:
        return None
    return re.sub(r"^([A-Z]*)0+(\d)", r"\1\2", s)


def normalize_po_number(raw: str | None) -> str | None:
    """'PO24001', 'po 24001', 'PO-24001', '24001' -> 'PO-24001'."""
    if not raw:
        return None
    m = re.search(r"(\d{4,8})", raw)
    return f"PO-{m.group(1)}" if m else None


def normalize_name(raw: str | None) -> str:
    """Lowercase, strip punctuation and legal suffixes: 'Northwind Industrial Supplies Ltd.' -> 'northwind industrial supplies'."""
    if not raw:
        return ""
    toks = re.sub(r"[^a-z0-9& ]", " ", raw.lower().replace("&", " and ")).split()
    toks = [t for t in toks if t not in LEGAL_SUFFIXES]
    return " ".join(toks)


def similarity(a: str, b: str) -> float:
    a, b = normalize_name(a) or a.lower(), normalize_name(b) or b.lower()
    return SequenceMatcher(None, a, b).ratio()


def desc_similarity(a: str, b: str) -> float:
    na = re.sub(r"[^a-z0-9 ]", " ", a.lower())
    nb = re.sub(r"[^a-z0-9 ]", " ", b.lower())
    na, nb = " ".join(na.split()), " ".join(nb.split())
    return SequenceMatcher(None, na, nb).ratio()


def fmt_money(value: Decimal | float | int | None, currency: str | None = None) -> str:
    if value is None:
        return "—"
    s = f"{Decimal(value):,.2f}"
    return f"{currency} {s}" if currency else s
