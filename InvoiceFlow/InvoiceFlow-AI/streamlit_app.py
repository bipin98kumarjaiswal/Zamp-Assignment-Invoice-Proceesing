"""InvoiceFlow AI - Streamlit entry point.  Run:  streamlit run streamlit_app.py"""
import streamlit as st

st.set_page_config(page_title="InvoiceFlow AI", page_icon=":material/receipt_long:", layout="wide",
                   initial_sidebar_state="expanded")

from app.config import get_settings  # noqa: E402
from app.db.engine import database_label, get_engine  # noqa: E402
from app.ui import components as ui  # noqa: E402
from app.ui import pages  # noqa: E402


def _sidebar() -> None:
    # brand first, then page links, then settings (navigation itself is hidden so we control the order)
    eng = get_engine()
    s = get_settings()
    with st.sidebar:
        st.markdown("<div class='brand'>InvoiceFlow AI</div><div class='brand-sub'>Invoice processing and decision automation</div>",
                    unsafe_allow_html=True)
    for key in ("overview", "process", "history", "review", "pos", "details"):
        pg = pages.PAGES[key]
        st.sidebar.page_link(pg, label=pg.title, icon=pg.icon)
    with st.sidebar:
        st.session_state.setdefault("actor", "analyst")
        st.text_input("Your name (for the audit trail)", key="actor")
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            ocr = "OCR ready" if s.ocr_enabled else "OCR disabled"
        except Exception:
            ocr = "OCR unavailable (Tesseract not installed)"
        ai = "AI extraction on" if (s.use_ai_extraction and s.openai_api_key) else "AI extraction off (rules only)"
        st.caption(f"Database: {database_label(eng)}  \n{ocr} · {ai}  \nPrice tolerance ±{s.price_tolerance_pct}%")



ui.inject_css()
P = st.Page
pages.PAGES.update({
    "overview": P(pages.page_overview, title="Overview", icon=":material/dashboard:", default=True),
    "process": P(pages.page_process, title="Process invoice", icon=":material/upload_file:", url_path="process"),
    "history": P(pages.page_history, title="Invoice history", icon=":material/history:", url_path="history"),
    "review": P(pages.page_review, title="Review center", icon=":material/rule:", url_path="review"),
    "pos": P(pages.page_pos, title="Purchase orders", icon=":material/inventory_2:", url_path="purchase-orders"),
    "details": P(pages.page_details, title="Run details", icon=":material/manage_search:", url_path="run-details"),
})
_sidebar()
st.navigation(list(pages.PAGES.values()), position="hidden").run()
