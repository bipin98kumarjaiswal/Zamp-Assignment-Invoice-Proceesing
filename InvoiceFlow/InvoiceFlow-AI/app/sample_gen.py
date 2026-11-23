"""Generates fictional invoice PDFs (clean text layouts A/B and a scanned variant). Needs reportlab (dev dependency)."""
from __future__ import annotations

import io
import random
from decimal import Decimal

from app.core.utils import q2

BILL_TO = ["Acme Manufacturing Co.", "Accounts Payable", "1 Corporate Plaza, Metropolis"]


def _fmt(d) -> str:
    return f"{Decimal(d):,.2f}"


def make_invoice_pdf(*, layout: str = "A", vendor: str, address: str, tax_id: str = "", invoice_no: str | None,
                     invoice_date: str | None, due_date: str | None, po: str | None, currency: str | None,
                     lines: list[tuple], tax_rate: Decimal | int | str = 0, discount: Decimal | str | None = None,
                     printed: dict | None = None, pages: int = 1, tax_label: str = "Tax") -> bytes:
    """`lines` = [(description, qty, unit_price)]. `printed` overrides printed amounts to fabricate arithmetic errors:
    keys subtotal / discount / tax / total / line_amounts (list)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    printed = printed or {}
    rows = [(d, Decimal(str(q)), Decimal(str(u)), q2(Decimal(str(q)) * Decimal(str(u)))) for d, q, u in lines]
    if "line_amounts" in printed:
        rows = [(d, q, u, Decimal(str(a))) for (d, q, u, _), a in zip(rows, printed["line_amounts"])]
    subtotal = q2(sum(r[3] for r in rows))
    disc = q2(Decimal(str(discount))) if discount else None
    net = subtotal - (disc or 0)
    tax = q2(net * Decimal(str(tax_rate)) / 100)
    total = q2(net + tax)
    subtotal_p = Decimal(str(printed.get("subtotal", subtotal)))
    tax_p = Decimal(str(printed.get("tax", tax)))
    total_p = Decimal(str(printed.get("total", total)))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    ink, muted = (0.1, 0.13, 0.19), (0.4, 0.43, 0.48)

    def txt(x, y, s, size=9, bold=False, right=False, color=ink):
        c.setFillColorRGB(*color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        (c.drawRightString if right else c.drawString)(x, y, s)

    # ---- header
    if layout == "A":
        txt(40, H - 50, vendor, 15, True)
        txt(40, H - 66, address, 9, color=muted)
        if tax_id:
            txt(40, H - 78, f"Tax ID: {tax_id}", 8, color=muted)
        txt(W - 40, H - 96, "INVOICE", 22, True, right=True)
        meta = [("Invoice No:", invoice_no), ("Invoice Date:", invoice_date), ("Due Date:", due_date),
                ("PO Number:", po), ("Currency:", currency)]
    else:
        txt(W / 2, H - 45, "TAX INVOICE", 18, True, right=False)
        txt(40, H - 80, f"From: {vendor}", 11, True)
        txt(40, H - 94, address, 9, color=muted)
        meta = [("Bill No:", invoice_no), ("Date of Issue:", invoice_date), ("Payment Due:", due_date), ("P.O. Ref:", po)]
    y = H - 135
    txt(40, y, "Bill To:", 9, True)
    for i, l in enumerate(BILL_TO):
        txt(40, y - 13 * (i + 1), l, 9)
    for i, (k, v) in enumerate(meta):
        if v:
            txt(360, y - 13 * i, k, 9, True)
            txt(440, y - 13 * i, str(v), 9)

    # ---- table
    ty = H - 260
    c.setStrokeColorRGB(0.75, 0.78, 0.82)
    c.line(40, ty - 4, W - 40, ty - 4)
    txt(40, ty, "Description", 9, True)
    txt(360, ty, "Qty", 9, True, right=True)
    txt(450, ty, "Unit Price", 9, True, right=True)
    txt(W - 40, ty, "Amount", 9, True, right=True)
    cy = ty - 20
    for d, q, u, a in rows:
        txt(40, cy, d)
        txt(360, cy, f"{q.normalize():f}", right=True)
        txt(450, cy, _fmt(u), right=True)
        txt(W - 40, cy, _fmt(a), right=True)
        cy -= 16
    c.line(40, cy + 8, W - 40, cy + 8)

    # ---- totals
    cy -= 14
    sub_label, tot_label = ("Subtotal", f"Total Due ({currency})" if currency else "Total Due") if layout == "A" else (
        "Net Amount", f"Amount Due {currency}" if currency else "Amount Due")
    txt(380, cy, sub_label)
    txt(W - 40, cy, _fmt(subtotal_p), right=True)
    cy -= 14
    if disc:
        txt(380, cy, "Discount")
        txt(W - 40, cy, "-" + _fmt(Decimal(str(printed.get("discount", disc)))), right=True)
        cy -= 14
    if Decimal(str(tax_rate)) or "tax" in printed:
        rate = Decimal(str(tax_rate)).normalize()
        txt(380, cy, f"{tax_label} ({rate:f}%)")
        txt(W - 40, cy, _fmt(tax_p), right=True)
        cy -= 14
    if not printed.get("omit_total"):
        txt(380, cy - 4, tot_label, 10, True)
        txt(W - 40, cy - 4, _fmt(total_p), 10, True, right=True)
    txt(40, 60, "Payment terms: Net 30 days. Thank you for your business.", 8, color=muted)
    txt(W - 40, 40, "Page 1", 8, right=True, color=muted)
    c.showPage()
    for p in range(2, pages + 1):
        txt(40, H - 50, f"{vendor} - continuation sheet", 9, color=muted)
        txt(W - 40, 40, f"Page {p}", 8, right=True, color=muted)
        c.showPage()
    c.save()
    return buf.getvalue()


def make_scanned(pdf_bytes: bytes, dpi: int = 200, seed: int = 7) -> bytes:
    """Rasterise a PDF to an image-only PDF with slight skew, blur and noise (simulates a scanner)."""
    import pymupdf as fitz
    from PIL import Image, ImageFilter

    rnd = random.Random(seed)
    out = fitz.open()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as src:
        for page in src:
            img = Image.open(io.BytesIO(page.get_pixmap(dpi=dpi).tobytes("png"))).convert("L")
            img = img.rotate(0.4, resample=Image.BICUBIC, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
            px = img.load()
            for _ in range(img.width * img.height // 150):
                x, y = rnd.randrange(img.width), rnd.randrange(img.height)
                px[x, y] = max(0, min(255, px[x, y] + rnd.randint(-40, 40)))
            b = io.BytesIO()
            img.save(b, "JPEG", quality=70)
            p = out.new_page(width=page.rect.width, height=page.rect.height)
            p.insert_image(p.rect, stream=b.getvalue())
    data = out.tobytes()
    out.close()
    return data


# ---------------------------------------------------------------- the seven documented demo invoices
def sample_specs() -> dict[str, dict]:
    D = Decimal
    return {
        "01-clean": dict(layout="A", vendor="Northwind Industrial Supplies Ltd", address="410 Harbor Way, Oakland, CA 94607",
                         tax_id="US-EIN-84-1100231", invoice_no="INV-10231", invoice_date="2026-11-03", due_date="2026-12-03",
                         po="PO-24001", currency="USD", tax_rate=8,
                         lines=[("Industrial gloves (carton)", 100, "12.50"), ("Safety goggles", 50, "8.40")]),
        "02-small-variance": dict(layout="A", vendor="Brightline Office Solutions Inc", address="88 Market Street, Austin, TX 78701",
                                  tax_id="US-EIN-47-2208841", invoice_no="INV-88412", invoice_date="2026-11-05",
                                  due_date="2026-12-05", po="PO-24002", currency="USD", tax_rate=7,
                                  lines=[("Copy paper A4 (box)", 200, "4.24"), ("Toner cartridge", 10, "62.00")]),
        "03-missing-po": dict(layout="A", vendor="Apex Logistics Partners LLC", address="1200 Freight Blvd, Chicago, IL 60609",
                              tax_id="US-EIN-36-9917720", invoice_no="INV-5530", invoice_date="2026-11-06",
                              due_date="2026-12-06", po=None, currency="USD", tax_rate=5,
                              lines=[("Pallet freight Chicago to Dallas", 12, "185.00"), ("Warehouse handling (hour)", 40, "32.50")]),
        "04-duplicate": dict(layout="B", vendor="Northwind Industrial Supplies Ltd", address="410 Harbor Way, Oakland, CA 94607",
                             invoice_no="INV10231", invoice_date="03 Nov 2026", due_date="03 Dec 2026", po="PO24001",
                             currency="USD", tax_rate=8, tax_label="Sales Tax",
                             lines=[("Industrial gloves (carton)", 100, "12.50"), ("Safety goggles", 50, "8.40")]),
        "05-vendor-mismatch": dict(layout="B", vendor="Brightline Office Solutions Inc", address="88 Market Street, Austin, TX 78701",
                                   invoice_no="BL-2210", invoice_date="08 Nov 2026", due_date="08 Dec 2026", po="PO-24004",
                                   currency="USD", tax_rate=8, tax_label="Sales Tax",
                                   lines=[("Managed hosting plan (month)", 12, "450.00")]),
        "06-over-budget": dict(layout="B", vendor="Meridian Software Services Pvt Ltd",
                               address="5th Floor, Tech Park, Whitefield, Bengaluru 560066",
                               invoice_no="MSS/2026/0457", invoice_date="10 Nov 2026", due_date="10 Dec 2026", po="PO-24005",
                               currency="INR", tax_rate=18, tax_label="GST",
                               lines=[("Implementation services (day)", 60, "2500.00")]),
        "07-scanned": dict(layout="A", vendor="Kestrel Facility Management Pvt Ltd",
                           address="21 Civil Lines, Kanpur, Uttar Pradesh 208001", tax_id="09FGHIJ5678K1Z2",
                           invoice_no="KFM-7781", invoice_date="2026-11-12", due_date="2026-12-12", po="PO-24006",
                           currency="INR", tax_rate=18, tax_label="GST",
                           lines=[("Housekeeping services (month)", 1, "85000.00"), ("Security services (month)", 1, "65000.00")]),
    }


def build_sample(name: str) -> bytes:
    pdf = make_invoice_pdf(**sample_specs()[name])
    return make_scanned(pdf) if name == "07-scanned" else pdf
