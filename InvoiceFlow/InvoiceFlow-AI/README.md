# InvoiceFlow AI

A small app I built that reads a vendor invoice PDF and decides what to do with it: **approve**, **send for review**, or **reject**. It also explains *why*, so a person can check the reasoning quickly.

I made this for the Zamp AI Solutions Analyst case study, **PS-1: Invoice processing, from PDF to decision**.

**Live app:** `[https://YOUR-APP-NAME.streamlit.app](https://zamp-assignment-invoice-proceesing-ldm272pjnb2gdmcybcmcff.streamlit.app/process)` 
![Process invoice](docs/screenshots/02-process-01-clean.png)

---

## The problem I picked

In a finance team, someone opens every emailed invoice, finds the matching purchase order (PO) in a spreadsheet, checks the numbers, and decides what to do. It is slow and boring, and a tired person can easily miss a duplicate or a wrong amount.

I wanted to automate the first pass, so people only look at the invoices that really need a human. The app never pays anything. It only gives a decision with the reasons.

## What it does

You upload a PDF and it goes through 10 steps, which you can watch live:

1. Document received (and hashed)
2. File validation
3. Text extraction (or OCR if it is a scan)
4. Pulling out the fields (vendor, invoice number, dates, line items, tax, total)
5. Data validation
6. Matching the vendor and purchase order
7. Financial checks (the maths, tolerances, PO balance)
8. Duplicate detection
9. Decision
10. Saving an audit record

Possible decisions:

- **Approved** if every rule passes.
- **Review** if something is missing, uncertain or doesn't add up.
- **Rejected** only for a confirmed duplicate of an already approved invoice, or a blocked vendor.

I kept "review" and "rejected" separate on purpose. Not being sure about an invoice is not the same as knowing it is bad.

## Design choices I made (and why)

- **The AI does not decide anything.** All the approval logic is normal code, so the same invoice always gives the same answer. There is an optional OpenAI step for extraction, but it is off by default. It can only fill fields that are missing, and the value has to appear in the document text.
- **It never guesses missing data.** If the invoice number, date, currency, total or PO is missing, the invoice goes to review. If the PO is missing, the app may *suggest* one but never applies it.
- **Scanned invoices always go to a person.** OCR can misread a digit, and one wrong digit changes a payment, so I don't auto-approve anything read by OCR.
- **There is no "approve" button in the Review Center.** A reviewer can correct things like the PO number or invoice number, and then the same rules run again. They can't edit amounts. This way nobody can approve a bad invoice just by clicking.
- **Duplicates are checked in several ways:** the file hash (so renaming the file doesn't help), vendor plus a cleaned-up invoice number (`INV-0042` and `inv42` count as the same), and vendor plus amount plus date. The database also has unique constraints, so two people uploading the same invoice at the same moment can't both get approved.
- **Currencies are never added together.** USD and INR totals are shown separately.

## The six pages

| Page | What it shows |
|---|---|
| Overview | Totals, value per currency, decision chart, trend, recent activity (all read from the database) |
| Process invoice | Upload a PDF, preview it, watch the stages run, see the decision and explanation |
| Invoice history | Search and filter all runs, download CSV, click a row to open it |
| Review center | Invoices that need a person, with reasons, evidence, and actions (correct and re-check, draft a vendor message, reject, add note) |
| Purchase orders | The 15 fictional POs with committed and remaining balances |
| Run details | Everything about one run, including the audit trail |

| Overview | Review center |
|---|---|
| ![Overview](docs/screenshots/03-overview.png) | ![Review center](docs/screenshots/06-review-detail.png) |

## Sample invoices

All data is made up. There are 7 PDFs in `sample_data/invoices/`. Process them in this order on a fresh database:

| # | File | What it tests | Result |
|---|---|---|---|
| 1 | `01-clean.pdf` | Everything correct | Approved |
| 2 | `02-small-variance.pdf` | Price 0.95% higher than the PO, within tolerance | Approved (with a note) |
| 3 | `03-missing-po.pdf` | No PO number on the invoice | Review |
| 4 | `04-duplicate.pdf` | Same invoice as #1 but a different layout and number format | Rejected |
| 5 | `05-vendor-mismatch.pdf` | PO belongs to a different vendor | Review |
| 6 | `06-over-budget.pdf` | More than the remaining PO balance | Review |
| 7 | `07-scanned.pdf` | Image-only scan, needs OCR | Review |

Note: `04` is only rejected because `01` was approved first. On a fresh database, `04` alone is a valid invoice and gets approved.

## How to run it on your computer

You need Python 3.11 or newer. Tesseract is only needed for the scanned sample.

```bash
# 1. (optional) install Tesseract for OCR
#    Windows: https://github.com/UB-Mannheim/tesseract/wiki  (add it to PATH)
#    Mac:     brew install tesseract
#    Ubuntu:  sudo apt-get install tesseract-ocr

# 2. create a virtual environment and install packages
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# 3. start the app
streamlit run streamlit_app.py
```

It opens at http://localhost:8501. On first start it creates a local SQLite database in `data/` and fills it with 9 vendors and 15 purchase orders. No API key is needed.

## Tests

```bash
python -m pytest tests -q
python scripts/run_sample_sequence.py
```

What I actually ran:

- 100 tests pass on SQLite, and the same 100 pass on a local PostgreSQL 16 (including tests for simultaneous uploads).
- 27 checks pass in a real browser (headless Chromium): uploading the 7 PDFs, search, the review flow, and data surviving a server restart.
- Browser testing found a real bug that the unit tests missed (a crash after a reviewer re-check), so I fixed it and added a test for it.

More detail is in `TEST_REPORT.md`.

## Settings

Settings can be environment variables, a local `.env` file, or Streamlit secrets. `.env.example` lists them all. Never commit real passwords or keys.

| Setting | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | local SQLite | Use a `postgresql://...` link for a hosted database |
| `PRICE_TOLERANCE_PCT` | 2.0 | Allowed price difference vs the PO |
| `MIN_FIELD_CONFIDENCE` | 0.80 | Below this, a key field is treated as uncertain |
| `MAX_FILE_MB` | 5 | Upload size limit |
| `USE_AI_EXTRACTION` | false | Optional AI-assisted extraction |
| `ALLOW_DB_RESET` | true for SQLite, false for PostgreSQL | Shows the reset button |

## Deployment

I deploy it on **Streamlit Community Cloud** with a free **Supabase** PostgreSQL database, so the history is kept even when the app restarts.

1. Push this repo to GitHub (the file `streamlit_app.py` must be at the top level).
2. Create a free Supabase project and copy the **Session pooler** connection string.
3. On share.streamlit.io, create an app from this repo with main file `streamlit_app.py`.
4. In the app's **Secrets**, add:
   ```toml
   DATABASE_URL = "postgresql://postgres.xxxx:YOURPASSWORD@aws-0-xx.pooler.supabase.com:5432/postgres"
   ALLOW_DB_RESET = "false"
   ```
5. `packages.txt` installs Tesseract automatically, so OCR works online too.

## Project structure

```
streamlit_app.py      app entry point
app/core/             extraction, parser, matching, validation, duplicates, decision, pipeline
app/db/               database schema, engine (SQLite/PostgreSQL), queries
app/ui/               the six pages and shared components
sample_data/          the 7 sample invoices and the vendor/PO data
scripts/              helper scripts (run samples, init database, generate samples)
tests/                pytest tests
docs/                 screenshots and browser test results
```

More detail: [ARCHITECTURE.md](ARCHITECTURE.md), [DEMO.md](DEMO.md), [TEST_REPORT.md](TEST_REPORT.md).

## Limitations

Being honest about what is not done:

- It works best on English invoices with clear labels. Unusual layouts will go to review rather than being guessed.
- Item descriptions that wrap onto two lines are not joined together.
- The PO balance is tracked per PO, not per line item.
- There is no login. The reviewer name is just typed in.
- The vendors and POs are fake demo data, not connected to a real ERP.
- "Request vendor info" only saves a draft message. It does not send an email.


## What I would do next

- Read invoices straight from an email inbox
- Connect to a real procurement or ERP system
- Match against goods receipts too (3-way match)
- Learn from reviewer corrections
- Add proper login and approval limits
