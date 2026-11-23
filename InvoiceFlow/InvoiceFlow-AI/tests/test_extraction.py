import dataclasses

from app.config import get_settings
from app.core.pdf_extract import extract_pdf, validate_file
from app.sample_gen import make_scanned
from tests.conftest import pdf, sample

S = get_settings()


def test_text_pdf_extracts_without_ocr():
    r = extract_pdf(pdf(), S)
    assert r.method == "text" and not r.errors and "INV-10231" in r.text and r.ocr_confidence is None


def test_scanned_pdf_uses_ocr_fallback():
    r = extract_pdf(sample("07-scanned"), S)
    assert r.method == "ocr" and r.ocr_pages == [1] and r.ocr_confidence > 60
    assert "KFM-7781" in r.text.replace(" ", "")


def test_multi_page_document():
    r = extract_pdf(pdf(pages=3), S)
    assert r.page_count == 3 and not r.errors and "continuation sheet" in r.text


def test_empty_bytes_blocked():
    assert validate_file(b"", "a.pdf", S)


def test_oversized_file_blocked():
    tiny = dataclasses.replace(S, max_file_mb=0)
    assert any("limit" in p for p in validate_file(pdf(), "a.pdf", tiny))


def test_non_pdf_blocked():
    assert validate_file(b"hello world", "a.pdf", S)


def test_corrupt_pdf_reports_error_not_exception():
    r = extract_pdf(b"%PDF-1.4\nthis is not really a pdf", S)
    assert r.errors


def test_blank_pdf_has_no_text_error():
    import io
    from reportlab.pdfgen import canvas
    b = io.BytesIO(); c = canvas.Canvas(b); c.showPage(); c.save()
    r = extract_pdf(b.getvalue(), S)
    assert r.errors and not r.lines


def test_too_many_pages_rejected():
    r = extract_pdf(pdf(pages=3), dataclasses.replace(S, max_pages=2))
    assert r.errors and "pages" in r.errors[0]


def test_ocr_unavailable_degrades_gracefully():
    r = extract_pdf(sample("07-scanned"), dataclasses.replace(S, ocr_enabled=False))
    assert r.errors and r.warnings
