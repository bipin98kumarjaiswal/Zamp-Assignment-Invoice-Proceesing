"""Engine factory, schema creation, seeding. Works unchanged on SQLite (local) and PostgreSQL (Supabase)."""
from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event, func, insert, select
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.core.utils import normalize_name, to_minor
from app.db import schema as S
from app.seed_data import PURCHASE_ORDERS, VENDORS


def make_engine(url: str | None = None) -> Engine:
    from app.config import normalize_db_url
    url = normalize_db_url(url or get_settings().database_url)
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2, pool_recycle=300, future=True)


@lru_cache(maxsize=1)
def _default_engine() -> Engine:
    eng = make_engine()
    init_db(eng)
    return eng


def get_engine() -> Engine:
    return _default_engine()


def is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


def init_db(engine: Engine, seed: bool = True) -> None:
    """Idempotent: creates missing tables and seeds master data only into an empty vendors table."""
    S.metadata.create_all(engine)
    if seed:
        with engine.begin() as conn:
            if conn.execute(select(func.count()).select_from(S.vendors)).scalar() == 0:
                seed_master_data(conn)


def seed_master_data(conn) -> None:
    for vid, name, status, country, tax_id, address in VENDORS:
        conn.execute(insert(S.vendors).values(
            id=vid, name=name, norm_name=normalize_name(name), status=status,
            country=country, tax_id=tax_id, address=address))
    for po, vid, cur, tax, status, committed, desc, lines in PURCHASE_ORDERS:
        net = sum(Decimal(q) * Decimal(p) for _, q, p in lines)
        conn.execute(insert(S.purchase_orders).values(
            po_number=po, vendor_id=vid, currency=cur, amount_net=to_minor(net),
            committed_net=to_minor(Decimal(committed)), tax_rate=tax, status=status, description=desc))
        for i, (d, q, p) in enumerate(lines, start=1):
            conn.execute(insert(S.po_lines).values(po_number=po, line_no=i, description=d, quantity=q, unit_price=p))


def reset_db(engine: Engine) -> None:
    """Drop everything and re-seed. Used by the demo 'reset' button and by tests."""
    S.metadata.drop_all(engine)
    init_db(engine)


def database_label(engine: Engine) -> str:
    """Safe-to-display description (never includes credentials)."""
    return "PostgreSQL (hosted)" if is_postgres(engine) else f"SQLite ({engine.url.database or 'memory'})"
