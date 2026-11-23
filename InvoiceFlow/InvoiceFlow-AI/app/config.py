"""Central configuration. Environment variables win over Streamlit secrets; secrets win over defaults.

Nothing secret is ever hard-coded here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

try:  # optional .env support for local development
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_URL = f"sqlite:///{ROOT / 'data' / 'invoiceflow.db'}"


def _secret(name: str, default: str | None = None) -> str | None:
    """Read env var, then st.secrets (if Streamlit is running and a secrets file exists)."""
    val = os.environ.get(name)
    if val not in (None, ""):
        return val
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


def normalize_db_url(url: str) -> str:
    """Make provider-style URLs SQLAlchemy-compatible (Supabase/Heroku give postgres://)."""
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str
    price_tolerance_pct: Decimal      # allowed unit-price variance vs PO, and PO-balance overrun (% of PO value)
    arithmetic_tolerance: Decimal     # absolute rounding tolerance for invoice arithmetic (currency units)
    min_field_confidence: float       # below this, a critical field is "uncertain" -> REVIEW
    max_file_mb: int
    max_pages: int
    ocr_enabled: bool
    ocr_dpi: int
    use_ai_extraction: bool
    openai_api_key: str | None
    openai_model: str
    allow_future_dates: bool


def get_settings() -> Settings:
    db = _secret("DATABASE_URL") or DEFAULT_SQLITE_URL
    return Settings(
        database_url=normalize_db_url(db),
        price_tolerance_pct=Decimal(_secret("PRICE_TOLERANCE_PCT", "2.0")),
        arithmetic_tolerance=Decimal(_secret("ARITHMETIC_TOLERANCE", "0.02")),
        min_field_confidence=float(_secret("MIN_FIELD_CONFIDENCE", "0.80")),
        max_file_mb=int(_secret("MAX_FILE_MB", "5")),
        max_pages=int(_secret("MAX_PAGES", "10")),
        ocr_enabled=(_secret("OCR_ENABLED", "true") or "true").lower() == "true",
        ocr_dpi=int(_secret("OCR_DPI", "200")),
        use_ai_extraction=(_secret("USE_AI_EXTRACTION", "false") or "false").lower() == "true",
        openai_api_key=_secret("OPENAI_API_KEY"),
        openai_model=_secret("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
        allow_future_dates=(_secret("ALLOW_FUTURE_DATES", "false") or "false").lower() == "true",
    )
