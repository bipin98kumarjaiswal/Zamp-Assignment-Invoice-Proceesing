"""PDF text extraction with OCR fallback. Never raises on bad input: returns errors in the result."""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from app.config import Settings

MIN_TEXT_CHARS_PER_PAGE = 40  # fewer extractable characters than this => treat the page as scanned


@dataclass
class ExtractionResult:
    lines: list[str] = field(default_factory=list)
    page_count: int = 0
    method: str = "none"              # text | ocr | text+ocr | none
    ocr_confidence: float | None = None   # 0-100 mean word confidence from Tesseract
    ocr_pages: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def used_ocr(self) -> bool:
        return bool(self.ocr_pages)


def validate_file(data: bytes, filename: str, settings: Settings) -> list[str]:
    """Cheap pre-checks. Returns a list of blocking problems (empty list = OK)."""
    problems = []
    if not data:
        problems.append("The file is empty (0 bytes).")
        return problems
    if len(data) > settings.max_file_mb * 1024 * 1024:
        problems.append(f"The file is {len(data) / 1024 / 1024:.1f} MB; the limit is {settings.max_file_mb} MB.")
    if not data[:1024].lstrip().startswith(b"%PDF"):
        problems.append("The file does not look like a PDF (missing %PDF header).")
    if not filename.lower().endswith(".pdf"):
        problems.append("The file name does not end in .pdf.")
    return problems


def _page_lines(page) -> list[str]:
    """Rebuild visual rows from word boxes so table cells on one row end up on one line."""
    words = page.get_text("words")
    if not words:
        return []
    items = sorted(((w[1] + w[3]) / 2, w[0], w[2], w[3] - w[1], w[4]) for w in words)
    rows: list[list[tuple]] = []
    for it in items:
        if rows and abs(it[0] - rows[-1][0][0]) <= max(2.5, it[3] * 0.45):
            rows[-1].append(it)
        else:
            rows.append([it])
    out = []
    for row in rows:
        row.sort(key=lambda r: r[1])
        parts, prev_x1 = [], None
        for _, x0, x1, _, word in row:
            if prev_x1 is not None:
                parts.append("  " if x0 - prev_x1 > 14 else " ")
            parts.append(word)
            prev_x1 = x1
        line = "".join(parts).strip()
        if line:
            out.append(line)
    return out


def _ocr_page(page, dpi: int):
    """OCR one page. Returns (lines, mean_confidence). Raises on missing Tesseract."""
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 6")
    rows: dict[tuple, list[tuple[int, str]]] = {}
    confs = []
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        rows.setdefault(key, []).append((data["left"][i], word))
        try:
            c = float(data["conf"][i])
            if c >= 0:
                confs.append(c)
        except (TypeError, ValueError):
            pass
    lines = [" ".join(w for _, w in sorted(r)) for _, r in sorted(rows.items())]
    return lines, (sum(confs) / len(confs) if confs else 0.0)


def extract_pdf(data: bytes, settings: Settings) -> ExtractionResult:
    res = ExtractionResult()
    try:
        import pymupdf as fitz  # PyMuPDF

        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        res.errors.append(f"The PDF could not be opened (it may be corrupt): {str(exc)[:120]}")
        return res
    try:
        if doc.needs_pass:
            res.errors.append("The PDF is password-protected and cannot be read.")
            return res
        res.page_count = doc.page_count
        if doc.page_count == 0:
            res.errors.append("The PDF has no pages.")
            return res
        if doc.page_count > settings.max_pages:
            res.errors.append(f"The PDF has {doc.page_count} pages; the limit is {settings.max_pages}.")
            return res
        confs, any_text = [], False
        for i, page in enumerate(doc, start=1):
            try:
                lines = _page_lines(page)
            except Exception as exc:  # pragma: no cover
                res.warnings.append(f"Page {i}: text extraction failed ({exc}).")
                lines = []
            if sum(len(x) for x in lines) >= MIN_TEXT_CHARS_PER_PAGE:
                any_text = True
                res.lines.extend(lines)
                continue
            if not settings.ocr_enabled:
                res.warnings.append(f"Page {i} has no text layer and OCR is disabled.")
                continue
            try:
                ocr_lines, conf = _ocr_page(page, settings.ocr_dpi)
            except Exception as exc:
                res.warnings.append(f"Page {i} looks scanned but OCR is unavailable: {type(exc).__name__}.")
                continue
            res.ocr_pages.append(i)
            confs.append(conf)
            res.lines.extend(ocr_lines + lines)
        if res.ocr_pages:
            res.ocr_confidence = round(sum(confs) / len(confs), 1)
            res.method = "text+ocr" if any_text else "ocr"
        elif any_text:
            res.method = "text"
        if not res.lines:
            res.errors.append("No readable text was found in the document (blank, image-only without OCR, or unreadable).")
        return res
    finally:
        doc.close()


def render_page_png(data: bytes, page: int = 0, zoom: float = 1.4) -> bytes | None:
    """PNG preview of one page for the UI. Returns None if the PDF cannot be rendered."""
    try:
        import pymupdf as fitz

        with fitz.open(stream=data, filetype="pdf") as doc:
            if not 0 <= page < doc.page_count:
                return None
            return doc[page].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
    except Exception:
        return None
