"""OPTIONAL AI-assisted extraction (off by default).

Safety design - the AI can never approve anything:
  * it only fills fields the rules could not find (never overrides a rule-based value);
  * every value it proposes must appear verbatim in the document text, otherwise it is discarded;
  * AI-filled fields get confidence 0.6 (< default 0.8 threshold) so the invoice is routed to REVIEW;
  * all financial checks stay deterministic code.
Requires `pip install openai` and OPENAI_API_KEY. Not exercised by the automated tests (no network/API key).
"""
from __future__ import annotations

import json
import re

from app.config import Settings
from app.core.models import Field_, InvoiceData
from app.core.utils import normalize_po_number, parse_date

PROMPT = ("Extract these fields from the invoice text and answer with a single JSON object with keys: "
          "vendor_name, invoice_number, invoice_date (as written), currency (ISO code), po_number. "
          "Use null for anything not explicitly present. Do not guess.")


def _in_text(value: str, text: str) -> bool:
    squash = lambda s: re.sub(r"\s+", "", s).lower()
    return bool(value) and squash(value) in squash(text)


def enhance_with_ai(text: str, inv: InvoiceData, settings: Settings) -> list[str]:
    notes: list[str] = []
    if not (settings.use_ai_extraction and settings.openai_api_key):
        return notes
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        out = client.chat.completions.create(
            model=settings.openai_model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": text[:12000]}])
        data = json.loads(out.choices[0].message.content)
    except Exception as exc:
        return [f"AI extraction skipped: {type(exc).__name__}"]
    for key in ("vendor_name", "invoice_number", "invoice_date", "currency"):
        val = data.get(key)
        if getattr(inv, key).value or not isinstance(val, str) or not _in_text(val, text):
            continue
        if key == "invoice_date":
            d, prob = parse_date(val)
            val = d.isoformat() if d else val
            setattr(inv, key, Field_(value=val, confidence=0.6, evidence="AI-extracted", source="ai", problem=prob))
        else:
            setattr(inv, key, Field_(value=val.strip(), confidence=0.6, evidence="AI-extracted", source="ai"))
        notes.append(f"AI filled '{key}' (verified present in text; confidence capped at 60%).")
    po = data.get("po_number")
    if not inv.po_numbers and isinstance(po, str) and _in_text(po, text) and normalize_po_number(po):
        inv.po_numbers, inv.po_confidence, inv.po_evidence = [normalize_po_number(po)], 0.6, "AI-extracted"
        notes.append("AI filled the PO reference (verified present in text).")
    return notes
