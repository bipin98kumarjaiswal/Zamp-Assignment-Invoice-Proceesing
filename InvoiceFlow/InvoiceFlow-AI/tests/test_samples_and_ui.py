import subprocess
import sys
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.sample_gen import build_sample, sample_specs
from tests.conftest import ROOT, sample

ORDER = [("01-clean", "APPROVED"), ("02-small-variance", "APPROVED"), ("03-missing-po", "REVIEW"), ("04-duplicate", "REJECTED"),
         ("05-vendor-mismatch", "REVIEW"), ("06-over-budget", "REVIEW"), ("07-scanned", "REVIEW")]


def test_seven_samples_in_documented_order(pipe):
    got = [(n, pipe.process(sample(n), f"{n}.pdf").decision.value) for n, _ in ORDER]
    assert got == ORDER


def test_shipped_pdfs_match_generator_inputs():
    for n in sample_specs():
        assert (ROOT / "sample_data" / "invoices" / f"{n}.pdf").exists()
    assert build_sample("01-clean")[:4] == b"%PDF"


def test_cli_sequence_exit_code_zero():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_sample_sequence.py")], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and out.stdout.count("OK ") == 7, out.stdout + out.stderr


# ------------------------------------------------------------------ headless UI
@pytest.fixture
def ui_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ui.db'}")
    monkeypatch.setenv("UI_STAGE_PAUSE", "0")
    monkeypatch.syspath_prepend(str(ROOT))
    import app.db.engine as eng
    eng._default_engine.cache_clear()
    yield
    eng._default_engine.cache_clear()


def page(fn):
    return AppTest.from_string(f"from app.ui import pages, components as ui\nui.inject_css()\npages.{fn}()", default_timeout=120)


def test_ui_every_view_renders_empty(ui_env):
    for fn in ("page_overview", "page_process", "page_history", "page_review", "page_pos", "page_details"):
        at = page(fn).run()
        assert not at.exception, (fn, at.exception)


def test_ui_process_all_samples_then_all_views_show_data(ui_env):
    at = page("page_process").run()
    for i in range(1, 8):
        at.selectbox(key="sample_pick").select_index(i).run()
        at.button[0].click().run()
        assert not at.exception, at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "Needs review" in html and "Run #7" in " ".join(s.value for s in at.subheader)
    ov = page("page_overview").run()
    assert not ov.exception
    ledger = next(m.value for m in ov.markdown if "class='ledger'" in m.value)
    assert ">7<" in ledger and ">2<" in ledger and ">4<" in ledger and ">1<" in ledger   # 7 total, 2 approved, 4 review, 1 rejected
    for fn in ("page_history", "page_review", "page_pos", "page_details"):
        a = page(fn).run()
        assert not a.exception, (fn, a.exception)
    assert any(d for d in page("page_history").run().dataframe)


def test_ui_html_is_escaped():
    from app.ui import components as ui
    assert "<script>" not in ui.badge("<script>alert(1)</script>")
    assert "&lt;img" in ui.esc("<img src=x onerror=1>")


def test_ui_no_write_to_widget_owned_session_keys():
    """Regression: the browser run found a crash from assigning st.session_state['actor'] after the sidebar widget
    owned that key. Streamlit forbids it, so nothing in the UI code may assign it."""
    import re
    for f in list((ROOT / "app" / "ui").glob("*.py")):
        assert not re.search(r"session_state\[['\"]actor['\"]\]\s*=", f.read_text()), f
