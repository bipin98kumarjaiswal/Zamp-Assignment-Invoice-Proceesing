from datetime import date
from decimal import Decimal

import pytest

from app.core.utils import (normalize_invoice_number, normalize_name, normalize_po_number, parse_date, parse_money, q2,
                            to_minor, from_minor)


@pytest.mark.parametrize("raw,exp", [("1,234.56", "1234.56"), ("$12.50", "12.50"), ("1.234,56", "1234.56"),
                                     ("1,50,000.00", "150000.00"), ("(50.00)", "-50.00"), ("12,5", "12.5"), ("abc", None)])
def test_parse_money(raw, exp):
    assert parse_money(raw) == (Decimal(exp) if exp else None)


def test_rounding_half_up():
    assert q2(Decimal("2.675")) == Decimal("2.68") and q2(Decimal("2.665")) == Decimal("2.67")


def test_minor_roundtrip():
    assert from_minor(to_minor(Decimal("1803.60"))) == Decimal("1803.60")


@pytest.mark.parametrize("raw,exp", [("2026-11-03", date(2026, 11, 3)), ("03 Nov 2026", date(2026, 11, 3)),
                                     ("Nov 3, 2026", date(2026, 11, 3)), ("25/12/2026", date(2026, 12, 25)),
                                     ("12/25/2026", date(2026, 12, 25))])
def test_parse_date_ok(raw, exp):
    assert parse_date(raw) == (exp, None)


@pytest.mark.parametrize("raw", ["03/04/2026", "2026-02-31", "2026-13-01", "yesterday"])
def test_parse_date_rejects_ambiguous_or_invalid(raw):
    d, problem = parse_date(raw)
    assert d is None and problem


def test_normalizers():
    assert normalize_invoice_number("INV-0231") == normalize_invoice_number("inv 231") == "INV231"
    assert normalize_invoice_number("INV-10231") == normalize_invoice_number("INV10231")
    assert normalize_po_number("po 24001") == "PO-24001" == normalize_po_number("PO24001")
    assert normalize_name("Northwind Industrial Supplies Ltd.") == "northwind industrial supplies"
