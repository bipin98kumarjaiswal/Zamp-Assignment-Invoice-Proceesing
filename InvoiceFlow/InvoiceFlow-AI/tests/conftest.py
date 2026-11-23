import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.pipeline import InvoicePipeline  # noqa: E402
from app.db.engine import init_db, make_engine  # noqa: E402
from app.sample_gen import make_invoice_pdf, sample_specs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


import os


def db_url(tmp_path, name="t.db") -> str:
    """SQLite file per test by default; set TEST_DATABASE_URL to run the whole suite on PostgreSQL instead."""
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / name}"


def fresh_engine(tmp_path, name="t.db"):
    from app.db.engine import reset_db
    from app.config import normalize_db_url
    e = make_engine(normalize_db_url(db_url(tmp_path, name)))
    reset_db(e)          # drop + create + seed: guarantees a clean database even on a shared PostgreSQL
    return e


@pytest.fixture
def eng(tmp_path):
    e = fresh_engine(tmp_path)
    yield e
    e.dispose()


@pytest.fixture
def pipe(eng):
    return InvoicePipeline(eng, get_settings())


def pdf(base: str = "01-clean", **over) -> bytes:
    spec = {**sample_specs()[base], **over}
    return make_invoice_pdf(**spec)


def sample(name: str) -> bytes:
    return (ROOT / "sample_data" / "invoices" / f"{name}.pdf").read_bytes()


def check(res, cid):
    return next(c for c in res.checks if c.id == cid)
