"""The six Streamlit views. All numbers come from the database; nothing is hard-coded."""
from __future__ import annotations

import hmac
import os
import time
from datetime import date, timedelta
from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st

from app.config import get_settings, _secret
from app.core.metrics import compute_metrics, filter_runs
from app.core.models import RunResult
from app.core.pipeline import STAGES, InvoicePipeline
from app.core.utils import fmt_money
from app.db import repo
from app.db.demo_admin import delete_invoice, reset_demo_history
from app.db.engine import get_engine
from app.ui import components as ui
from app.ui.samples import SAMPLES, sample_bytes

PAGES: dict = {}   # filled by streamlit_app.py so views can switch pages
COLORS = {"Approved": "#1F7A4D", "Needs review": "#C48A12", "Rejected": "#A8323A"}
LABEL = {"APPROVED": "Approved", "REVIEW": "Needs review", "REJECTED": "Rejected"}
STAGE_PAUSE = float(os.environ.get("UI_STAGE_PAUSE", "0.18"))  # cosmetic pacing only; results are real


def _engine():
    return get_engine()


def _pipeline() -> InvoicePipeline:
    return InvoicePipeline(_engine(), get_settings())


def _actor() -> str:
    return (st.session_state.get("actor") or "analyst").strip() or "analyst"


def _load(run_id: int) -> RunResult | None:
    with _engine().connect() as conn:
        return repo.load_result(conn, run_id)


def _doc(run_id: int) -> bytes | None:
    with _engine().connect() as conn:
        return repo.fetch_document(conn, run_id)


def _go_details(run_id: int) -> None:
    ui.open_run(run_id)
    st.switch_page(PAGES["details"])


# ============================================================================ Admin demo tools
def _demo_management(runs: list[dict]) -> None:
    # Never expose destructive controls on a public deployment without a secret.
    password = _secret("ADMIN_PASSWORD")
    if not password:
        return
    with st.expander("Demo management (admin only)", expanded=False):
        if not st.session_state.get("demo_admin_authenticated", False):
            with st.form("demo_login"):
                candidate = st.text_input("Admin password", type="password")
                login = st.form_submit_button("Unlock demo tools")
            if login:
                if hmac.compare_digest(candidate, password):
                    st.session_state["demo_admin_authenticated"] = True
                    st.rerun()
                else:
                    st.error("Incorrect admin password.")
            return

        if st.button("Lock demo tools", key="demo_logout"):
            st.session_state["demo_admin_authenticated"] = False
            st.rerun()
        st.warning("Demo data only. These actions permanently remove invoices, documents and audit history.")
        if runs:
            lookup = {int(r["id"]): r for r in runs}
            selected = st.selectbox(
                "Invoice run to delete", list(lookup),
                format_func=lambda rid: f"#{rid} · {lookup[rid]['filename']} · {lookup[rid]['decision']}",
                key="demo_delete_run",
            )
            st.caption("Deleting a run also deletes its related re-validation history. An approved invoice's PO commitment is reversed.")
            with st.form("demo_delete_form"):
                confirmation = st.text_input(f"Type DELETE {selected} to confirm")
                submitted = st.form_submit_button("Delete selected invoice")
            if submitted:
                if confirmation != f"DELETE {selected}":
                    st.error("Confirmation did not match; nothing deleted.")
                else:
                    try:
                        count = delete_invoice(_engine(), int(selected))
                    except Exception as exc:
                        st.error(f"Deletion cancelled: {exc}")
                    else:
                        for key in ("last_run_id", "selected_run", "hist_tbl"):
                            st.session_state.pop(key, None)
                        st.success(f"Deleted {count} related run(s).")
                        st.rerun()
        else:
            st.info("There are no processed invoices to delete.")

        with st.form("demo_reset_form"):
            st.markdown("**Reset demo history**")
            st.caption("Clears every processing run and review action. Demo vendors and purchase orders remain, with initial balances restored.")
            reset_confirmation = st.text_input("Type RESET DEMO to confirm")
            reset_submitted = st.form_submit_button("Reset all demo invoices")
        if reset_submitted:
            if reset_confirmation != "RESET DEMO":
                st.error("Confirmation did not match; nothing reset.")
            else:
                try:
                    count = reset_demo_history(_engine())
                except Exception as exc:
                    st.error(f"Reset cancelled: {exc}")
                else:
                    for key in ("last_run_id", "selected_run", "hist_tbl"):
                        st.session_state.pop(key, None)
                    st.success(f"Removed {count} runs and restored demo PO balances.")
                    st.rerun()


# ============================================================================ A. Overview
def page_overview() -> None:
    ui.page_header("Overview", "Live totals from every invoice processed so far. Nothing on this page is hard-coded.")
    with _engine().connect() as conn:
        runs = repo.list_runs(conn)
    m = compute_metrics(runs)
    c = m["counts"]
    ui.ledger([
        ("Invoices processed", str(m["total"]), "#17212B", f"{m['runs_total']} runs incl. re-validations"),
        ("Approved", str(c["APPROVED"]), "#1F7A4D", "passed every rule"),
        ("Needs review", str(c["REVIEW"]), "#B07A10", f"{m['open_review']} still open in the queue"),
        ("Rejected", str(c["REJECTED"]), "#A8323A", "duplicates, blocked vendors, reviewer rejections"),
    ])
    _demo_management(runs)
    if not runs:
        st.markdown("<div class='callout'>No invoices processed yet. Open <b>Process invoice</b> and try one of the sample PDFs.</div>",
                    unsafe_allow_html=True)
        if st.button("Process the first invoice", type="primary"):
            st.switch_page(PAGES["process"])
        return

    left, right = st.columns([5, 7], gap="large")
    with left:
        st.subheader("Invoice value by currency")
        cur = sorted(set(m["submitted_value"]) | set(m["approved_value"]))
        st.dataframe(pd.DataFrame([{"Currency": k, "Submitted": float(m["submitted_value"].get(k, 0)),
                                    "Approved": float(m["approved_value"].get(k, 0))} for k in cur]),
                     hide_index=True, width="stretch",
                     column_config={"Submitted": st.column_config.NumberColumn(format="%,.2f"),
                                    "Approved": st.column_config.NumberColumn(format="%,.2f")})
        st.caption("Currencies are never added together. Totals include tax, as printed on the invoice.")
        st.subheader("Decision distribution")
        dist = pd.DataFrame({"Decision": [LABEL[k] for k in c], "Invoices": list(c.values())})
        st.altair_chart(alt.Chart(dist).mark_bar(size=22).encode(
            y=alt.Y("Decision:N", sort=list(COLORS), title=None), x=alt.X("Invoices:Q", title=None, axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("Decision:N", scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values())), legend=None),
            tooltip=["Decision", "Invoices"]).properties(height=130), width="stretch")
    with right:
        st.subheader("Processing trend")
        rows = [{"Day": d.isoformat(), "Decision": LABEL[k], "Invoices": n} for d, v in m["daily"].items() for k, n in v.items() if n]
        st.altair_chart(alt.Chart(pd.DataFrame(rows)).mark_bar(size=48).encode(
            x=alt.X("Day:O", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("Invoices:Q", title=None, axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("Decision:N", scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values())), legend=alt.Legend(orient="bottom", title=None)),
            tooltip=["Day", "Decision", "Invoices"]).properties(height=250), width="stretch")
        st.subheader("Recent activity")
        recent = ui.runs_df(runs[:8])[["Run", "Invoice no.", "Vendor", "Amount", "Currency", "Decision", "Processed (UTC)"]]
        st.dataframe(recent, hide_index=True, width="stretch",
                     column_config={"Amount": st.column_config.NumberColumn(format="%,.2f")})


# ============================================================================ B. Process invoice
def page_process() -> None:
    ui.page_header("Process invoice", "Upload a vendor invoice PDF. The 10 stages below run for real and the saved result is what you see.")
    s = get_settings()
    left, right = st.columns([4, 7], gap="large")
    data, name = None, None
    with left:
        up = st.file_uploader("Invoice PDF", type=["pdf"], help=f"Max {s.max_file_mb} MB, up to {s.max_pages} pages. Text or scanned.")
        labels = {f"{f}  ·  {desc}": (f, exp) for f, desc, exp in SAMPLES}
        pick = st.selectbox("…or try a bundled sample", ["—"] + list(labels), key="sample_pick")
        if up is not None:
            data, name = up.getvalue(), up.name
        elif pick != "—":
            name = labels[pick][0]
            data = sample_bytes(name)
            st.caption(f"Expected result: **{LABEL[labels[pick][1]]}**" + (" (only after sample 01 is approved)" if name.startswith("04") else ""))
        go = st.button("Process invoice", type="primary", disabled=data is None, width="stretch")
        if data:
            with st.expander("Document preview", expanded=True):
                ui.pdf_preview(data, "proc")
    with right:
        tl = st.empty()
        if go and data is not None:
            stages: list = []
            tl.markdown(ui.timeline_html([], STAGES, running=1), unsafe_allow_html=True)

            def on_stage(stage) -> None:
                stages.append(stage)
                tl.markdown(ui.timeline_html(stages, STAGES, running=min(10, len(stages) + 1)), unsafe_allow_html=True)
                time.sleep(STAGE_PAUSE)

            with st.spinner("Processing…"):
                res = _pipeline().process(data, name, actor=_actor(), on_stage=on_stage)
            st.session_state["last_run_id"] = res.run_id
            tl.markdown(ui.timeline_html(res.stages, STAGES), unsafe_allow_html=True)
        elif st.session_state.get("last_run_id"):
            last = _load(st.session_state["last_run_id"])
            if last:
                tl.markdown(ui.timeline_html(last.stages, STAGES), unsafe_allow_html=True)
                st.caption(f"Showing the saved result of run #{last.run_id} ({last.filename}).")
        else:
            tl.markdown(ui.timeline_html([], STAGES), unsafe_allow_html=True)

    rid = st.session_state.get("last_run_id")
    if rid:
        res = _load(rid)
        if res:
            st.divider()
            h1, h2 = st.columns([6, 2])
            h1.subheader(f"Run #{res.run_id} · {res.filename}")
            if h2.button("Open run details", key="proc_open", width="stretch"):
                _go_details(res.run_id)
            if res.decision.value == "REVIEW":
                st.markdown("<div class='callout warn'>This invoice is waiting in the <b>Review Center</b>. A reviewer can correct identity fields and re-validate; "
                            "there is deliberately no way to approve it by hand.</div>", unsafe_allow_html=True)
            ui.show_run(res, "proc")


# ============================================================================ C. History
def page_history() -> None:
    ui.page_header("Invoice history", "Every processing run, searchable. Select a row to inspect it.")
    with _engine().connect() as conn:
        runs = repo.list_runs(conn)
    if not runs:
        st.info("Nothing to show yet. Process an invoice first.")
        return
    c1, c2, c3, c4 = st.columns([3, 3, 2, 2])
    q = c1.text_input("Search", placeholder="invoice no., vendor, PO, file name or run #")
    dec = c2.multiselect("Decision", list(LABEL), format_func=lambda k: LABEL[k])
    vendor = c3.selectbox("Vendor", ["All"] + sorted({r["vendor_name"] for r in runs if r.get("vendor_name")}))
    cur = c4.selectbox("Currency", ["All"] + sorted({r["currency"] for r in runs if r.get("currency")}))
    d1, d2, _ = st.columns([2, 2, 5])
    start = d1.date_input("From", value=None, key="h_from")
    end = d2.date_input("To", value=None, key="h_to")
    rows = filter_runs(runs, q, dec, None if vendor == "All" else vendor, None if cur == "All" else cur, start, end)
    st.caption(f"{len(rows)} of {len(runs)} runs")
    df = ui.runs_df(rows)
    if df.empty:
        st.info("No runs match these filters.")
        return
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="hist_tbl",
                      column_config={"Amount": st.column_config.NumberColumn(format="%,.2f")})
    st.download_button("Download CSV", df.to_csv(index=False).encode(), "invoiceflow_history.csv", "text/csv")
    sel = ev.selection.rows if ev and ev.selection else []
    if sel:
        rid = int(df.iloc[sel[0]]["Run"])
        res = _load(rid)
        st.divider()
        h1, h2 = st.columns([6, 2])
        h1.subheader(f"Run #{rid} · {res.filename}")
        if h2.button("Open run details", key="hist_open", width="stretch"):
            _go_details(rid)
        ui.show_run(res, "hist")


# ============================================================================ D. Review Center
def _vendor_draft(res: RunResult) -> str:
    inv = res.invoice
    issues = [c for c in res.checks if c.status.value in ("FAIL", "UNCERTAIN")]
    asks = list(dict.fromkeys(c.next_action for c in issues if c.next_action and ("vendor" in c.next_action.lower())))
    lines = [f"Subject: Invoice {inv.invoice_number.value or '(number not found)'} - information needed", "",
             f"Hello {inv.vendor_name.value or 'team'},", "",
             f"We could not process your invoice {inv.invoice_number.value or ''} "
             f"(dated {inv.invoice_date.value or 'unknown'}, total {fmt_money(Decimal(inv.total.value), inv.currency.value) if inv.total.value else 'unknown'}) because:"]
    lines += [f"  - {c.detail}" for c in issues if c.category != "duplicate"] or ["  - (see attached review notes)"]
    lines += ["", "Please reply with a corrected invoice or the missing information:"] + [f"  - {a}" for a in asks]
    lines += ["", "Kind regards,", "Accounts Payable"]
    return "\n".join(lines)


def page_review() -> None:
    ui.page_header("Review center", "Invoices that need a person. Every action here is saved to the audit trail.")
    with _engine().connect() as conn:
        runs = repo.list_runs(conn)
    queue = [r for r in runs if r["decision"] == "REVIEW" and r["review_state"] in ("OPEN", "INFO_REQUESTED")]
    if not queue:
        st.markdown("<div class='callout'>The review queue is empty. Invoices that cannot be approved or rejected automatically will appear here.</div>",
                    unsafe_allow_html=True)
        return
    st.caption(f"{len(queue)} invoice(s) waiting")
    qdf = ui.runs_df(queue)[["Run", "Invoice no.", "Vendor", "Amount", "Currency", "Review status", "File"]]
    qdf["Reason"] = [r.get("summary_line", "").replace("Needs review: ", "") for r in queue]
    ev = st.dataframe(qdf, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="rev_tbl",
                      column_config={"Amount": st.column_config.NumberColumn(format="%,.2f")})
    sel = ev.selection.rows if ev and ev.selection else []
    if not sel:
        st.markdown("<div class='callout'>Select an invoice above to see why it needs review and what to do next.</div>", unsafe_allow_html=True)
        return
    rid = int(qdf.iloc[sel[0]]["Run"])
    res = _load(rid)
    problems = [c for c in res.checks if c.status.value in ("FAIL", "UNCERTAIN")]
    st.divider()
    st.subheader(f"Run #{rid} · {res.invoice.invoice_number.value or res.filename} · {res.invoice.vendor_name.value or 'unknown vendor'}")
    a, b = st.columns([7, 4], gap="large")
    with a:
        st.markdown(f"<div class='callout warn'><b>Why it is here:</b> {ui.esc(res.summary_line.replace('Needs review: ', ''))}</div>", unsafe_allow_html=True)
        st.markdown("**Failed or uncertain checks**")
        st.markdown(ui.checks_html(problems), unsafe_allow_html=True)
        st.markdown("**Suggested next action**")
        for na in res.next_actions:
            st.markdown(f"- {na}")
        sugg = res.po_snapshot.get("suggested_pos")
        if sugg:
            st.info("Engine suggestion (not applied): " + ", ".join(f"{s['po_number']} (remaining {s['currency']} {s['remaining']})" for s in sugg))
        t1, t2, t3 = st.tabs(["Extracted information", "Purchase order", "Financials"])
        with t1:
            ui.show_fields(res)
        with t2:
            ui.show_po(res)
        with t3:
            ui.show_financials(res)
    with b:
        with st.expander("Source document", expanded=True):
            ui.pdf_preview(_doc(rid), f"rev{rid}")

    st.subheader("Reviewer actions")
    st.markdown("<div class='callout'>There is no <b>Approve</b> button on purpose. A reviewer corrects the identity fields the engine could not trust "
                "(vendor, invoice number, date, currency, PO) and the <b>same deterministic rules</b> run again. Amounts can never be edited here: "
                "if an amount is wrong, reject the invoice or request a corrected one.</div>", unsafe_allow_html=True)
    inv = res.invoice
    used_ocr = "ocr" in res.extraction_method
    tab_fix, tab_ask, tab_rej, tab_note = st.tabs(["Correct and re-validate", "Request vendor info", "Reject invoice", "Add note"])
    with tab_fix:
        with st.form(f"fix_{rid}"):
            f1, f2 = st.columns(2)
            vendor = f1.text_input("Vendor name", inv.vendor_name.value or "")
            invno = f2.text_input("Invoice number", inv.invoice_number.value or "")
            idate = f1.text_input("Invoice date (YYYY-MM-DD)", inv.invoice_date.value or "")
            cur = f2.text_input("Currency (ISO code)", inv.currency.value or "")
            po = f1.text_input("PO number", inv.po_numbers[0] if len(inv.po_numbers) == 1 else "",
                               placeholder="e.g. PO-24003", help="Leave blank to keep what was extracted.")
            who = f2.text_input("Reviewer name", _actor())
            ocr = st.checkbox("I compared the amounts and details with the scanned page and they are correct") if used_ocr else False
            note = st.text_area("Justification (required)", placeholder="What did you check, and why is the correction right?")
            ok = st.form_submit_button("Re-validate with these values", type="primary")
        if ok:
            cur_vals = {"vendor_name": inv.vendor_name.value or "", "invoice_number": inv.invoice_number.value or "",
                        "invoice_date": inv.invoice_date.value or "", "currency": inv.currency.value or "",
                        "po_number": inv.po_numbers[0] if len(inv.po_numbers) == 1 else ""}
            new_vals = {"vendor_name": vendor, "invoice_number": invno, "invoice_date": idate, "currency": cur, "po_number": po}
            changed = {k: v.strip() for k, v in new_vals.items() if v.strip() and v.strip() != cur_vals[k]}
            try:
                child = _pipeline().revalidate(rid, who, note, changed, ocr_verified=ocr)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.markdown(ui.stamp(child.decision.value), unsafe_allow_html=True)
                st.success(f"Re-validated as run #{child.run_id}. {child.summary_line}")
                if child.decision.value == "REVIEW":
                    st.caption("It remains in the queue with its remaining issues.")
                st.button("Open the new run", key=f"open_child_{rid}", on_click=ui.open_run, args=(child.run_id,))
    with tab_ask:
        draft = st.text_area("Draft message to the vendor", _vendor_draft(res), height=260, key=f"draft_{rid}")
        who2 = st.text_input("Reviewer name", _actor(), key=f"who_ask_{rid}")
        if st.button("Log this request", key=f"ask_{rid}"):
            if not who2.strip():
                st.error("Enter your name.")
            else:
                repo.add_review_action(_engine(), rid, "INFO_REQUESTED", who2.strip(), draft, "INFO_REQUESTED")
                st.success("Logged. InvoiceFlow does not send email; copy the draft into your mail client.")
    with tab_rej:
        why = st.text_area("Reason for rejection (required)", key=f"why_{rid}")
        who3 = st.text_input("Reviewer name", _actor(), key=f"who_rej_{rid}")
        if st.button("Reject invoice", key=f"rej_{rid}"):
            if not why.strip() or not who3.strip():
                st.error("A reason and your name are required.")
            else:
                repo.add_review_action(_engine(), rid, "REJECTED", who3.strip(), why.strip(), "REJECTED_BY_REVIEWER")
                st.success("Invoice rejected and recorded. It has left the queue.")
    with tab_note:
        nt = st.text_area("Note", key=f"note_{rid}")
        who4 = st.text_input("Reviewer name", _actor(), key=f"who_note_{rid}")
        if st.button("Save note", key=f"savenote_{rid}"):
            if not nt.strip() or not who4.strip():
                st.error("A note and your name are required.")
            else:
                repo.add_review_action(_engine(), rid, "NOTE", who4.strip(), nt.strip())
                st.success("Note saved.")
    _audit_block(rid)


def _audit_block(rid: int) -> None:
    with _engine().connect() as conn:
        acts, audit = repo.list_review_actions(conn, rid), repo.audit_for_run(conn, rid)
    st.subheader("Audit trail")
    rows = [{"When (UTC)": ui._ts(a["created_at"]), "Event": a["event"], "Who": a["actor"], "Detail": (a["detail"] or "")[:300]} for a in audit]
    rows += [{"When (UTC)": ui._ts(a["created_at"]), "Event": "ACTION " + a["action"], "Who": a["actor"], "Detail": (a["note"] or "")[:300]} for a in acts]
    if rows:
        st.dataframe(pd.DataFrame(sorted(rows, key=lambda r: r["When (UTC)"])), hide_index=True, width="stretch")
    else:
        st.caption("No audit events.")


# ============================================================================ E. Purchase orders
def page_pos() -> None:
    ui.page_header("Purchase orders", "The fictional procurement dataset the engine matches against. Committed amounts rise as invoices are approved.")
    with _engine().connect() as conn:
        pos = repo.list_purchase_orders(conn)
    c1, c2 = st.columns([2, 2])
    statuses = c1.multiselect("Status", sorted({p["status"] for p in pos}))
    cur = c2.selectbox("Currency", ["All"] + sorted({p["currency"] for p in pos}))
    pos = [p for p in pos if (not statuses or p["status"] in statuses) and (cur == "All" or p["currency"] == cur)]
    df = pd.DataFrame([{"PO": p["po_number"], "Vendor": p["vendor_name"], "Vendor status": p["vendor_status"],
                        "Currency": p["currency"], "Original amount": float(p["amount_net"]),
                        "Committed": float(p["committed_net"]), "Remaining": float(p["remaining"]),
                        "Used": float(p["committed_net"] / p["amount_net"]) if p["amount_net"] else 0.0,
                        "Tax %": float(p["tax_rate"]), "Status": p["status"], "Description": p["description"]} for p in pos])
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="po_tbl", column_config={
        "Original amount": st.column_config.NumberColumn(format="%,.2f"), "Committed": st.column_config.NumberColumn(format="%,.2f"),
        "Remaining": st.column_config.NumberColumn(format="%,.2f"),
        "Used": st.column_config.ProgressColumn("Used", min_value=0, max_value=1, format="percent")})
    st.caption("Amounts are net of tax, in each PO's own currency. Select a PO to see its lines and linked invoices.")
    sel = ev.selection.rows if ev and ev.selection else []
    if sel:
        po_no = df.iloc[sel[0]]["PO"]
        with _engine().connect() as conn:
            po = repo.get_po(conn, po_no)
            linked = [r for r in repo.list_runs(conn) if r.get("po_number") == po_no]
        st.subheader(f"{po_no} · {po['description']}")
        st.dataframe(pd.DataFrame([{"Line": l["line_no"], "Description": l["description"], "Quantity": float(l["quantity"]),
                                    "Unit price": float(l["unit_price"]), "Amount": float(l["amount"])} for l in po["lines"]]),
                     hide_index=True, width="stretch")
        st.markdown("**Invoices processed against this PO**")
        if linked:
            st.dataframe(ui.runs_df(linked)[["Run", "Invoice no.", "Amount", "Currency", "Decision", "Processed (UTC)"]], hide_index=True, width="stretch")
        else:
            st.caption("None yet.")


# ============================================================================ F. Run details
def page_details() -> None:
    ui.page_header("Run details", "Everything the engine saw, calculated and decided for one run.")
    with _engine().connect() as conn:
        runs = repo.list_runs(conn)
    if not runs:
        st.info("No runs yet. Process an invoice first.")
        return
    ids = [r["id"] for r in runs]
    default = st.session_state.get("selected_run")
    idx = ids.index(default) if default in ids else 0
    meta = {r["id"]: r for r in runs}
    rid = st.selectbox("Run", ids, index=idx, format_func=lambda i: f"#{i} · {meta[i]['filename']} · {LABEL[meta[i]['decision']]}")
    st.session_state["selected_run"] = rid
    res = _load(rid)
    a, b = st.columns([6, 4], gap="large")
    with a:
        info = [("Run", f"#{res.run_id}"), ("File", res.filename), ("File size", f"{res.file_size:,} bytes"),
                ("SHA-256", res.doc_hash), ("Pages", str(res.page_count)), ("Extraction method", res.extraction_method +
                 (f" (OCR confidence {res.ocr_confidence}%)" if res.ocr_confidence else "")),
                ("Processed (UTC)", (res.created_at or "")[:19].replace("T", " ")), ("Processing time", f"{res.duration_ms} ms"),
                ("Processed by", res.actor), ("Decision", LABEL[res.decision.value]),
                ("Re-validation of run", f"#{res.parent_run_id}" if res.parent_run_id else "— (original submission)"),
                ("Duplicate of run", f"#{res.duplicate_of_run}" if res.duplicate_of_run else "—")]
        st.dataframe(pd.DataFrame(info, columns=["Property", "Value"]), hide_index=True, width="stretch")
        children = [r["id"] for r in runs if r.get("parent_run_id") == rid]
        if children:
            st.caption("Re-validated as run(s): " + ", ".join(f"#{c}" for c in children))
    with b:
        with st.expander("Source document", expanded=True):
            ui.pdf_preview(_doc(rid), f"det{rid}")
    ui.show_run(res, f"det{rid}")
    _audit_block(rid)
