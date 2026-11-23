# InvoiceFlow AI
**Intelligent invoice processing and decision automation** — Zamp AI Solutions Analyst case study, problem statement PS-1 (Finance / AP: *Invoice processing — from PDF to decision*).

Upload a vendor invoice PDF. InvoiceFlow extracts the data (text layer, or OCR for scans), matches it to a purchase order, runs deterministic financial and duplicate checks, and produces a **reasoned, auditable decision**: `APPROVED`, `REVIEW` or `REJECTED`. It never pays anything.

## What makes it trustworthy
| Principle | How it is enforced |
|---|---|
| AI never approves | Approval comes only from deterministic rules in `app/core`. The optional OpenAI step can only *fill missing fields*, each value must appear verbatim in the document, and its confidence is capped below the approval threshold. |
| Never invent data | A missing invoice number, date, currency, total or PO stays missing and forces `REVIEW`. A PO is never guessed (suggestions are shown, not applied). |
| Uncertainty → human | Low-confidence fields, OCR-read documents, ambiguous POs/vendors, conflicting figures → `REVIEW`. |
| No back door | The Review Center has **no approve button**. Reviewers correct identity fields (vendor, number, date, currency, PO) and the *same rules re-run*. Amounts cannot be edited. |
| Duplicates can't slip through | Checks on file hash (renaming does nothing), vendor + normalised invoice number, and vendor + amount + date. Database unique constraints close the concurrent-submission race. |
| Everything is explained | Each run stores 10 stage results, every check with evidence, the arithmetic, the explanation and the next action. |

## Features (six views)
Overview dashboard · Process invoice (live 10-stage run view) · Invoice history (search/filter/CSV) · Review center · Purchase orders (15 fictional POs) · Run details (full audit of one run).

## Decision rules (summary)
* **APPROVED** — all required fields found with ≥80% confidence; vendor ACTIVE and in master; PO exists, OPEN/PARTIALLY_BILLED, owned by that vendor, same currency; lines match PO lines; qty ≤ PO qty; unit prices within ±2%; tax rate equals PO; line×price, subtotal, discount, tax and total reconcile within 0.02; net amount ≤ remaining PO balance (+2% of PO value tolerance); no duplicate; not OCR-read.
* **REJECTED** — confirmed duplicate of an *approved* invoice (same file hash, or same vendor + normalised invoice number), or vendor status BLOCKED.
* **REVIEW** — everything else that fails or is uncertain. Rejection is never used for "needs a human".

Tolerances, rounding rule (half-up, 2 dp) and all assumptions are in `ARCHITECTURE.md`.

## Edge cases built and demonstrated
1. **Re-formatted duplicate** (`04-duplicate.pdf`): different layout, date format and invoice-number punctuation, so the file hash differs, yet it is caught and rejected.
2. **Scanned invoice** (`07-scanned.pdf`): OCR fallback reads it at ~95% confidence, but policy sends OCR-read invoices to a person; the reviewer attests and the rules re-run.
3. **Over budget vs remaining PO balance** (`06-over-budget.pdf`) — plus cumulative commitments, split billing and concurrent approvals (race guards).
4. **Vendor/PO mismatch** (`05`) and **missing PO** (`03`, with a non-binding suggestion).

## Quick start (local, no API key needed)
Requires Python 3.11+ and the Tesseract OCR binary (only needed for scanned PDFs).

```bash
# 1. Tesseract (optional but recommended)
#    Ubuntu/Debian: sudo apt-get install tesseract-ocr
#    macOS:         brew install tesseract
#    Windows:       install from https://github.com/UB-Mannheim/tesseract/wiki and add it to PATH

# 2. Environment
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt  # runtime + pytest + reportlab

# 3. Run
streamlit run streamlit_app.py       # opens http://localhost:8501
```
A local SQLite database is created at `data/invoiceflow.db` on first start and seeded with 9 vendors and 15 POs.

## Run the tests
```bash
python -m pytest tests -q                       # SQLite (default)
TEST_DATABASE_URL=postgresql://user:pw@localhost:5432/db python -m pytest tests -q   # same suite on PostgreSQL
python scripts/run_sample_sequence.py           # 7 sample invoices, fresh DB, exits 0 only if all decisions match
```

## Configuration (environment variables or Streamlit secrets)
| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | local SQLite | `postgresql://…` for Supabase/Postgres (`postgres://` also accepted) |
| `PRICE_TOLERANCE_PCT` | `2.0` | unit-price variance allowed vs PO; PO-balance overrun allowed as % of PO value |
| `ARITHMETIC_TOLERANCE` | `0.02` | absolute rounding tolerance when re-adding invoice figures |
| `MIN_FIELD_CONFIDENCE` | `0.80` | critical fields below this force REVIEW |
| `MAX_FILE_MB` / `MAX_PAGES` | `5` / `10` | upload limits |
| `OCR_ENABLED` / `OCR_DPI` | `true` / `200` | OCR fallback |
| `USE_AI_EXTRACTION`, `OPENAI_API_KEY`, `OPENAI_MODEL` | off | optional AI-assisted extraction (`pip install -r requirements-ai.txt`) |
| `ALLOW_DB_RESET` | on for SQLite, off for Postgres | shows "Reset demo database" |
| `UI_STAGE_PAUSE` | `0.18` | cosmetic pause between stages in the live view (results are always real); `0` disables |

See `.env.example`. **Never commit real credentials.**

## Project layout
```
streamlit_app.py            entry point (Streamlit Cloud: select this file)
app/core/                   extraction, parser, matching, validation, duplicates, decision, pipeline, metrics
app/db/                     schema, engine (SQLite/Postgres), repository (all SQL)
app/ui/                     components + the six views
app/seed_data.py            fictional vendors and purchase orders
app/sample_gen.py           invoice PDF generator (clean layouts A/B and scanned)
sample_data/                7 invoice PDFs + vendors.csv + purchase_orders.json
scripts/                    generate_samples, run_sample_sequence, init_db, export_schema
tests/                      100 pytest tests
db/                         generated schema SQL (Postgres, SQLite)
```
More detail: `ARCHITECTURE.md`, `DEMO.md`, `TEST_REPORT.md`, `DEPLOYMENT_GUIDE.md`, `GITHUB_UPLOAD_GUIDE.md`.

## Known limitations
English-label invoices only; single-line descriptions (wrapped item descriptions are not joined); PO balance is tracked at PO level, not per line; no email sending (the vendor message is a draft you copy); no login/roles; emails and ERP integrations are out of scope. See `ARCHITECTURE.md`.
