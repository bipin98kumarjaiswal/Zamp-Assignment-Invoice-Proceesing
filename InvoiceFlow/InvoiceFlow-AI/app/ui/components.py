"""Shared UI building blocks. Every value that came from a document or the database is HTML-escaped."""
from __future__ import annotations

import html
import json
from decimal import Decimal

import pandas as pd
import streamlit as st

from app.core.models import RunResult
from app.core.utils import fmt_money

DECISION = {
    "APPROVED": ("Approved", "#1F7A4D", "#E2F2E9"),
    "REVIEW": ("Needs review", "#8A5A00", "#FBEFD0"),
    "REJECTED": ("Rejected", "#A8323A", "#F7E1E3"),
}
CHECK = {  # glyph, colour, background
    "PASS": ("✓", "#1F7A4D", "#E2F2E9"), "WARN": ("!", "#8A5A00", "#FBEFD0"), "FAIL": ("✕", "#A8323A", "#F7E1E3"),
    "UNCERTAIN": ("?", "#8A5A00", "#FBEFD0"), "REJECT": ("✕", "#A8323A", "#F7E1E3"), "SKIPPED": ("–", "#5B6672", "#ECEFF2"),
}
STAGE = {"success": ("✓", "#1F7A4D"), "warning": ("!", "#8A5A00"), "failed": ("✕", "#A8323A"),
         "skipped": ("–", "#8A949E"), "pending": ("·", "#B5BDC5"), "running": ("…", "#12435A")}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,500;6..72,600&family=Public+Sans:wght@400;500;600&display=swap');
.stApp, .stApp [data-testid="stMarkdownContainer"] *, .stApp label, .stApp input, .stApp textarea, .stApp button p,
.stApp [data-testid="stSidebar"] a span:not([data-testid="stIconMaterial"]) { font-family: 'Public Sans', system-ui, -apple-system, 'Segoe UI', sans-serif; }
h1, h2, h3 { font-weight: 600 !important; letter-spacing: -0.01em; color: #17212B; }
.stApp h1, .stApp h2, .stApp h3, .stApp .brand, .stApp .stamp, .stApp .ledger .val { font-family: 'Newsreader', Georgia, 'Times New Roman', serif !important; }
h1 { font-size: 2.1rem !important; margin-bottom: .1rem !important; }
.block-container { padding-top: 2.2rem; max-width: 1240px; }
[data-testid="stSidebar"] { background: #12303F; }
[data-testid="stSidebar"] * { color: #DCE6EC; }
[data-testid="stSidebar"] input, [data-testid="stSidebar"] textarea { color: #17212B !important; background: #F3F5F7; }
[data-testid="stSidebar"] [data-testid="stExpander"] * { color: #DCE6EC; }
[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] { border-radius: 6px; }
[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"][aria-current="page"] { background: rgba(255,255,255,.12); }
.num, .tabnum { font-variant-numeric: tabular-nums; }
.if-sub { color:#5B6672; margin: 0 0 1.2rem 0; font-size: .98rem; max-width: 70ch; }
.brand { font-family:'Newsreader',serif; font-size:1.5rem; font-weight:600; color:#fff; line-height:1.1; margin:.2rem 0 0 0; }
.brand-sub { font-size:.78rem; color:#9DB4C1; margin-bottom:.8rem; }
.pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:.78rem; font-weight:600; white-space:nowrap; }
.ledger { display:flex; background:#fff; border:1px solid #D5DCE2; border-radius:6px; margin: .4rem 0 1.2rem 0; }
.ledger > div { flex:1; padding: 14px 18px; border-right:1px solid #E1E6EA; }
.ledger > div:last-child { border-right:none; }
.ledger .lbl { font-size:.8rem; color:#5B6672; margin-bottom:2px; }
.ledger .val { font-family:'Newsreader',serif; font-size:2.1rem; font-weight:600; line-height:1.1; font-variant-numeric: tabular-nums; }
.ledger .hint { font-size:.76rem; color:#7A8591; }
.stamp { display:inline-block; border:3px solid; border-radius:6px; padding:6px 18px; font-family:'Newsreader',serif; font-size:1.7rem; font-weight:600;
         letter-spacing:.04em; text-transform:uppercase; transform: rotate(-2deg); margin:.2rem 0 .6rem 0; opacity:.92; }
.timeline { background:#fff; border:1px solid #D5DCE2; border-radius:6px; padding:6px 0; }
.tl-row { display:flex; gap:12px; padding:7px 16px; align-items:baseline; border-bottom:1px solid #EEF1F4; }
.tl-row:last-child { border-bottom:none; }
.tl-glyph { width:22px; height:22px; border-radius:50%; text-align:center; line-height:22px; font-weight:700; font-size:.8rem; flex:none; color:#fff; }
.tl-name { font-weight:600; width:210px; flex:none; font-size:.92rem; }
.tl-sum { color:#3B4752; font-size:.88rem; flex:1; }
.tl-ms { color:#8A949E; font-size:.78rem; width:64px; text-align:right; flex:none; font-variant-numeric: tabular-nums; }
.tl-pending .tl-name, .tl-pending .tl-sum { color:#9AA4AD; font-weight:400; }
table.checks { width:100%; border-collapse:collapse; background:#fff; border:1px solid #D5DCE2; font-size:.88rem; }
table.checks td, table.checks th { padding:7px 10px; border-bottom:1px solid #EEF1F4; vertical-align:top; text-align:left; }
table.checks th { background:#EEF2F5; font-weight:600; font-size:.8rem; color:#3B4752; }
table.checks tr.cat td { background:#F6F8FA; font-weight:600; color:#12435A; font-size:.8rem; }
.chip { display:inline-block; min-width:24px; text-align:center; border-radius:4px; font-weight:700; padding:1px 6px; }
.muted { color:#6A7580; font-size:.85rem; }
.ev { color:#6A7580; font-size:.78rem; margin-top:2px; }
.callout { border-left:4px solid #12435A; background:#E9F0F4; padding:10px 14px; border-radius:0 6px 6px 0; margin:.4rem 0 1rem 0; font-size:.92rem; }
.callout.warn { border-left-color:#B07A10; background:#FBF3DE; }
.callout.bad { border-left-color:#A8323A; background:#F9E8EA; }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def page_header(title: str, subtitle: str) -> None:
    st.markdown(f"# {esc(title)}")
    st.markdown(f"<p class='if-sub'>{esc(subtitle)}</p>", unsafe_allow_html=True)


def badge(decision: str) -> str:
    label, fg, bg = DECISION.get(decision, (decision, "#333", "#eee"))
    return f"<span class='pill' style='color:{fg};background:{bg}'>{esc(label)}</span>"


def stamp(decision: str) -> str:
    label, fg, _ = DECISION.get(decision, (decision, "#333", "#eee"))
    return f"<div class='stamp' style='color:{fg};border-color:{fg}'>{esc(label)}</div>"


def ledger(items: list[tuple[str, str, str, str]]) -> None:
    """items: (label, value, colour, hint)"""
    cells = "".join(f"<div><div class='lbl'>{esc(l)}</div><div class='val' style='color:{c}'>{esc(v)}</div>"
                    f"<div class='hint'>{esc(h)}</div></div>" for l, v, c, h in items)
    st.markdown(f"<div class='ledger'>{cells}</div>", unsafe_allow_html=True)


def timeline_html(stages: list, all_stages: list[tuple[int, str, str]], running: int | None = None) -> str:
    done = {s.n: s for s in stages}
    rows = []
    for n, _, title in all_stages:
        if n in done:
            s = done[n]
            g, col = STAGE[s.status]
            rows.append(f"<div class='tl-row'><div class='tl-glyph' style='background:{col}'>{g}</div>"
                        f"<div class='tl-name'>{n}. {esc(s.name)}</div><div class='tl-sum'>{esc(s.summary)}</div>"
                        f"<div class='tl-ms'>{s.duration_ms} ms</div></div>")
        else:
            is_run = running == n
            g, col = STAGE["running" if is_run else "pending"]
            rows.append(f"<div class='tl-row tl-pending'><div class='tl-glyph' style='background:{col}'>{g}</div>"
                        f"<div class='tl-name'>{n}. {esc(title)}</div><div class='tl-sum'>{'Running…' if is_run else 'Waiting'}</div>"
                        f"<div class='tl-ms'></div></div>")
    return f"<div class='timeline'>{''.join(rows)}</div>"


def checks_html(checks: list, categories: set[str] | None = None) -> str:
    names = {"file": "File", "extraction": "Extraction", "data": "Data validation", "po_match": "Vendor & purchase order",
             "financial": "Financial validation", "duplicate": "Duplicate detection"}
    rows, last = [], None
    for c in checks:
        cat = c.category if hasattr(c, "category") else c["category"]
        if categories and cat not in categories:
            continue
        g = c if hasattr(c, "status") else None
        status = c.status.value if hasattr(c.status, "value") else c.status
        glyph, fg, bg = CHECK[status]
        if cat != last:
            rows.append(f"<tr class='cat'><td colspan='3'>{esc(names.get(cat, cat))}</td></tr>")
            last = cat
        ev = f"<div class='ev'>Evidence: {esc(c.evidence)}</div>" if c.evidence else ""
        na = f"<div class='ev'>Next: {esc(c.next_action)}</div>" if c.next_action and status not in ("PASS", "WARN") else ""
        rows.append(f"<tr><td style='width:46px'><span class='chip' style='color:{fg};background:{bg}'>{glyph}</span></td>"
                    f"<td style='width:230px'><b>{esc(c.name)}</b><div class='ev'>{esc(status.title())}</div></td>"
                    f"<td>{esc(c.detail)}{ev}{na}</td></tr>")
    if not rows:
        return "<p class='muted'>No checks recorded.</p>"
    return "<table class='checks'><tr><th></th><th>Check</th><th>Result</th></tr>" + "".join(rows) + "</table>"


def fields_df(res: RunResult) -> pd.DataFrame:
    inv = res.invoice
    rows = []
    for key, label in [("vendor_name", "Vendor name"), ("vendor_address", "Vendor address"), ("invoice_number", "Invoice number"),
                       ("invoice_date", "Invoice date"), ("due_date", "Due date"), ("currency", "Currency"),
                       ("subtotal", "Subtotal"), ("discount", "Discount"), ("tax_rate", "Tax rate %"),
                       ("tax_amount", "Tax amount"), ("total", "Invoice total")]:
        f = getattr(inv, key)
        rows.append({"Field": label, "Value": f.value or "— not found —", "Confidence": f.confidence if f.value else 0.0,
                     "Source": f.source if f.value else "", "Evidence (source line)": f.evidence or "", "Note": f.problem or ""})
    rows.insert(5, {"Field": "PO reference", "Value": ", ".join(inv.po_numbers) or "— not found —",
                    "Confidence": inv.po_confidence, "Source": "rules" if inv.po_numbers else "",
                    "Evidence (source line)": inv.po_evidence or "",
                    "Note": "multiple POs found" if len(inv.po_numbers) > 1 else ""})
    return pd.DataFrame(rows)


def show_fields(res: RunResult) -> None:
    st.dataframe(fields_df(res), hide_index=True, width="stretch", column_config={
        "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1, format="percent")})
    if res.invoice.line_items:
        comp = {r["description"]: r for r in res.calc.get("line_comparison", [])}
        rows = []
        for it in res.invoice.line_items:
            c = comp.get(it.description, {})
            rows.append({"Description": it.description, "Qty": float(it.quantity or 0), "Unit price": float(it.unit_price or 0),
                         "Line total": float(it.amount or 0), "PO unit price": float(c["po_unit_price"]) if c else None,
                         "Variance %": float(c["variance_pct"]) if c else None})
        st.markdown("**Line items**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "Qty": st.column_config.NumberColumn(format="%.2f"), "Unit price": st.column_config.NumberColumn(format="%.2f"),
            "Line total": st.column_config.NumberColumn(format="%.2f"), "PO unit price": st.column_config.NumberColumn(format="%.2f"),
            "Variance %": st.column_config.NumberColumn(format="%+.2f")})
    else:
        st.info("No itemised line items were recognised in this document.")


def show_financials(res: RunResult) -> None:
    c, cur = res.calc, res.invoice.currency.value
    if not c:
        st.info("Financial validation did not run for this document.")
        return
    m = lambda k: fmt_money(Decimal(c[k]), cur) if c.get(k) not in (None, "") else "—"
    rows = [("Sum of line totals", m("lines_sum")), ("Subtotal" + (" (derived)" if c.get("subtotal_derived") else ""), m("subtotal")),
            ("Discount", m("discount")), ("Net amount (basis for PO balance)", m("net_amount")), ("Tax amount", m("tax_amount")),
            ("Expected total (net + tax)", m("expected_total")),
            ("Printed total", fmt_money(Decimal(res.invoice.total.value), cur) if res.invoice.total.value else "—")]
    if c.get("po_amount"):
        rows += [("PO amount (net)", m("po_amount")), ("Already committed", m("po_committed")), ("Remaining balance", m("po_remaining")),
                 (f"Balance tolerance ({c['price_tolerance_pct']}% of PO)", m("balance_allowance"))]
    rows += [("Rounding rule", c.get("rounding", "")), ("Arithmetic tolerance", c.get("arithmetic_tolerance", ""))]
    st.dataframe(pd.DataFrame(rows, columns=["Calculation", "Value"]), hide_index=True, width="stretch")


def show_po(res: RunResult) -> None:
    po = res.po_snapshot
    if not po or not po.get("po_number"):
        sugg = po.get("suggested_pos") if po else None
        st.warning("No purchase order was matched." + (
            f" Suggested (not applied): {', '.join(s['po_number'] for s in sugg)}." if sugg else ""))
        return
    cur = po.get("currency")
    st.markdown(f"**{esc(po['po_number'])}** — {esc(po.get('description'))} &nbsp; {badge('APPROVED') if po.get('status') in ('OPEN', 'PARTIALLY_BILLED') else badge('REVIEW')} "
                f"<span class='muted'>{esc(po.get('status'))}</span>", unsafe_allow_html=True)
    cols = st.columns(4)
    cols[0].metric("PO amount (net)", fmt_money(Decimal(po["amount_net"]), cur))
    cols[1].metric("Committed", fmt_money(Decimal(po["committed_net"]), cur))
    cols[2].metric("Remaining", fmt_money(Decimal(po["remaining"]), cur))
    cols[3].metric("Tax rate", f"{po['tax_rate']}%")
    st.dataframe(pd.DataFrame([{"Line": l["line_no"], "Description": l["description"], "Qty": float(l["quantity"]),
                                "Unit price": float(l["unit_price"]), "Amount": float(l["amount"])} for l in po.get("lines", [])]),
                 hide_index=True, width="stretch")


def show_decision(res: RunResult) -> None:
    st.markdown(stamp(res.decision.value), unsafe_allow_html=True)
    st.markdown(res.explanation)


def show_run(res: RunResult, key: str = "run") -> None:
    """The full explanation of a saved run. Used by Process, History, Review Center and Run Details."""
    show_decision(res)
    t1, t2, t3, t4, t5, t6 = st.tabs(["Checks", "Extracted fields", "PO match", "Financials", "Stage log", "Raw text"])
    with t1:
        st.markdown(checks_html(res.checks), unsafe_allow_html=True)
    with t2:
        st.caption(f"Extraction method: **{res.extraction_method}**" + (f" · OCR confidence {res.ocr_confidence}%" if res.ocr_confidence else "")
                   + f" · {res.page_count} page(s)")
        show_fields(res)
    with t3:
        show_po(res)
    with t4:
        show_financials(res)
    with t5:
        from app.core.pipeline import STAGES
        st.markdown(timeline_html(res.stages, STAGES), unsafe_allow_html=True)
        if res.warnings or res.errors:
            for e in res.errors:
                st.error(e)
            for w in res.warnings:
                st.warning(w)
    with t6:
        st.code(res.raw_text or "(no text extracted)", language=None)


def pdf_preview(data: bytes | None, key: str) -> None:
    from app.core.pdf_extract import render_page_png
    if not data:
        st.caption("Document not available for preview.")
        return
    png = render_page_png(data, 0)
    if png is None:
        st.warning("Preview unavailable: the PDF could not be rendered.")
        return
    try:
        import pymupdf
        with pymupdf.open(stream=data, filetype="pdf") as d:
            n = d.page_count
    except Exception:
        n = 1
    page = 1
    if n > 1:
        page = st.number_input("Page", 1, n, 1, key=f"pg_{key}")
        png = render_page_png(data, int(page) - 1) or png
    st.image(png, width="stretch", caption=f"Page {page} of {n}")


def runs_df(runs: list[dict]) -> pd.DataFrame:
    rows = []
    for r in runs:
        rows.append({"Run": r["id"], "Invoice no.": r.get("invoice_number") or "—", "Vendor": r.get("vendor_name") or "—",
                     "Invoice date": r.get("invoice_date") or "—", "PO": r.get("po_number") or "—",
                     "Amount": float(r["total"]) if r.get("total") is not None else None, "Currency": r.get("currency") or "—",
                     "Decision": DECISION.get(r["decision"], (r["decision"],))[0],
                     "Review status": {"NONE": "", "OPEN": "Open", "INFO_REQUESTED": "Info requested", "RESOLVED": "Re-validated",
                                       "REJECTED_BY_REVIEWER": "Rejected by reviewer"}.get(r.get("review_state"), r.get("review_state")),
                     "Processed (UTC)": _ts(r["created_at"]), "File": r["filename"]})
    return pd.DataFrame(rows)


def _ts(v) -> str:
    try:
        return v.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(v)[:19]


def open_run(run_id: int) -> None:
    st.session_state["selected_run"] = run_id
