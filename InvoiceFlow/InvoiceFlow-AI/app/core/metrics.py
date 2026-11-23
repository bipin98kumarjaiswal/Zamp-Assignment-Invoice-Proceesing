"""Dashboard maths and history filtering. Pure functions over run dicts from repo.list_runs()."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

OPEN_STATES = {"OPEN", "INFO_REQUESTED"}


def _day(v) -> date:
    return v.date() if isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def effective_runs(runs: list[dict]) -> list[dict]:
    """One row per *submitted invoice*.

    A reviewer re-validation creates a child run. The invoice's effective outcome is that of the latest run in its
    chain; if a reviewer rejected the latest run, the effective outcome is REJECTED.
    """
    by_parent: dict[int, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("parent_run_id"):
            by_parent[r["parent_run_id"]].append(r)
    out = []
    for root in (r for r in runs if not r.get("parent_run_id")):
        cur = root
        while cur["id"] in by_parent:
            cur = max(by_parent[cur["id"]], key=lambda x: x["id"])
        outcome = "REJECTED" if cur.get("review_state") == "REJECTED_BY_REVIEWER" else cur["decision"]
        out.append({**cur, "root_id": root["id"], "submitted_at": root["created_at"], "outcome": outcome,
                    "needs_attention": outcome == "REVIEW" and cur.get("review_state") in OPEN_STATES})
    return sorted(out, key=lambda r: r["root_id"], reverse=True)


def compute_metrics(runs: list[dict]) -> dict:
    eff = effective_runs(runs)
    counts = {"APPROVED": 0, "REVIEW": 0, "REJECTED": 0}
    submitted: dict[str, Decimal] = defaultdict(Decimal)
    approved: dict[str, Decimal] = defaultdict(Decimal)
    daily: dict[date, dict[str, int]] = defaultdict(lambda: {"APPROVED": 0, "REVIEW": 0, "REJECTED": 0})
    for r in eff:
        counts[r["outcome"]] += 1
        daily[_day(r["submitted_at"])][r["outcome"]] += 1
        if r.get("total") is not None and r.get("currency"):
            submitted[r["currency"]] += r["total"]
            if r["outcome"] == "APPROVED":
                approved[r["currency"]] += r["total"]
    return {"total": len(eff), "counts": counts, "open_review": sum(1 for r in eff if r["needs_attention"]),
            "submitted_value": dict(submitted), "approved_value": dict(approved),
            "daily": {d: v for d, v in sorted(daily.items())}, "runs_total": len(runs)}


def filter_runs(runs: list[dict], query: str = "", decisions: list[str] | None = None, vendor: str | None = None,
                currency: str | None = None, start: date | None = None, end: date | None = None) -> list[dict]:
    q = (query or "").strip().lower()
    out = []
    for r in runs:
        if decisions and r["decision"] not in decisions:
            continue
        if vendor and r.get("vendor_name") != vendor:
            continue
        if currency and r.get("currency") != currency:
            continue
        d = _day(r["created_at"])
        if start and d < start or end and d > end:
            continue
        if q and q not in " ".join(str(r.get(k) or "") for k in
                                   ("invoice_number", "vendor_name", "po_number", "filename", "id")).lower():
            continue
        out.append(r)
    return out
