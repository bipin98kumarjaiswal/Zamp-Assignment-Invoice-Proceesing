"""Stage 6 - vendor identity and purchase-order matching. Conservative: ambiguity always goes to a human."""
from __future__ import annotations

import re
from decimal import Decimal

from app.core.models import Check, CheckStatus as C, InvoiceData
from app.core.utils import desc_similarity, normalize_name, similarity
from app.db import repo

VENDOR_MATCH = 0.92       # >= : same vendor
VENDOR_POSSIBLE = 0.75    # >= : "possibly the same" -> human decides
LINE_MATCH = 0.85         # invoice line <-> PO line description similarity
OPEN_PO_STATUSES = {"OPEN", "PARTIALLY_BILLED"}


def resolve_vendor(name: str | None, vendors: list[dict]) -> tuple[dict | None, float, list[tuple[str, float]]]:
    if not name:
        return None, 0.0, []
    scored = sorted(((similarity(name, v["name"]), v) for v in vendors), key=lambda x: -x[0])
    cands = [(v["name"], round(s, 2)) for s, v in scored[:3] if s >= 0.5]
    if not scored or scored[0][0] < VENDOR_MATCH:
        return None, scored[0][0] if scored else 0.0, cands
    if len(scored) > 1 and scored[1][0] >= VENDOR_MATCH and scored[0][0] - scored[1][0] < 0.03:
        return None, scored[0][0], cands  # two near-identical master records: refuse to guess
    return scored[0][1], scored[0][0], cands


def match_lines(inv_lines, po_lines) -> list[dict]:
    """Greedy one-to-one assignment of invoice lines to PO lines by description similarity."""
    pairs = sorted(((desc_similarity(il.description, pl["description"]), i, j)
                    for i, il in enumerate(inv_lines) for j, pl in enumerate(po_lines)), reverse=True)
    used_i, used_j, out = set(), set(), {}
    for score, i, j in pairs:
        if i in used_i or j in used_j or score < 0.6:
            continue
        used_i.add(i); used_j.add(j)
        out[i] = {"inv_index": i, "po_line_no": po_lines[j]["line_no"], "po_index": j, "score": round(score, 3)}
    return [out.get(i, {"inv_index": i, "po_line_no": None, "po_index": None, "score": 0.0}) for i in range(len(inv_lines))]


def suggest_po_candidates(inv: InvoiceData, vendor_id: str | None, conn) -> list[dict]:
    """Used only when the invoice names no PO: which open POs of this vendor contain all invoice lines?"""
    if not vendor_id or not inv.line_items:
        return []
    out = []
    for po in repo.pos_for_vendor(conn, vendor_id):
        if po["status"] not in OPEN_PO_STATUSES:
            continue
        m = match_lines(inv.line_items, po["lines"])
        if all(x["score"] >= LINE_MATCH for x in m):
            out.append({"po_number": po["po_number"], "remaining": str(po["remaining"]), "currency": po["currency"]})
    return out


def run_matching(inv: InvoiceData, conn) -> dict:
    """Returns {checks, vendor, po, line_matches, candidates}."""
    checks: list[Check] = []
    vendors = repo.all_vendors(conn)
    name = inv.vendor_name.value
    vendor, score, cands = resolve_vendor(name, vendors)

    # ---- vendor identity
    if not name:
        checks.append(Check(id="vendor_identity", category="po_match", name="Vendor identified in vendor master",
                            status=C.FAIL, detail="No vendor name could be extracted from the invoice.",
                            next_action="Open the document, identify the issuing vendor and re-validate."))
    elif vendor:
        raw = lambda x: " ".join(re.sub(r"[^a-z0-9 ]", "", x.lower()).split())
        exact = raw(name) == raw(vendor["name"])
        note = "" if exact else f" (name differs from master: '{vendor['name']}'; similarity {score:.2f}; check legal suffix/spelling)"
        checks.append(Check(id="vendor_identity", category="po_match", name="Vendor identified in vendor master",
                            status=C.PASS if exact else C.WARN, detail=f"Matched to {vendor['id']} {vendor['name']}{note}.",
                            actual=name, expected=vendor["name"], evidence=inv.vendor_name.evidence))
    else:
        poss = f" Closest records: {', '.join(f'{n} ({s})' for n, s in cands)}." if cands else ""
        checks.append(Check(id="vendor_identity", category="po_match", name="Vendor identified in vendor master",
                            status=C.FAIL, detail=f"'{name}' is not an approved vendor in the vendor master.{poss}",
                            actual=name, evidence=inv.vendor_name.evidence,
                            next_action="Confirm the vendor identity with Procurement; onboard the vendor if it is new."))

    # ---- vendor status
    if vendor:
        st = vendor["status"]
        if st == "BLOCKED":
            checks.append(Check(id="vendor_status", category="po_match", name="Vendor status", status=C.REJECT,
                                detail=f"{vendor['name']} is BLOCKED in the vendor master. Invoices from blocked vendors are rejected.",
                                actual=st, expected="ACTIVE", next_action="Do not pay. Notify Procurement/Compliance."))
        elif st != "ACTIVE":
            checks.append(Check(id="vendor_status", category="po_match", name="Vendor status", status=C.FAIL,
                                detail=f"{vendor['name']} is {st} in the vendor master.", actual=st, expected="ACTIVE",
                                next_action="Procurement must reactivate the vendor before this invoice can be paid."))
        else:
            checks.append(Check(id="vendor_status", category="po_match", name="Vendor status", status=C.PASS,
                                detail="Vendor is ACTIVE.", actual=st, expected="ACTIVE"))
    else:
        checks.append(Check(id="vendor_status", category="po_match", name="Vendor status", status=C.SKIPPED,
                            detail="Vendor was not identified, so status cannot be checked."))

    # ---- PO reference
    po = None
    candidates: list[dict] = []
    refs = inv.po_numbers
    if len(refs) == 0:
        candidates = suggest_po_candidates(inv, vendor["id"] if vendor else None, conn)
        hint = ""
        if len(candidates) == 1:
            hint = f" A single open PO of this vendor matches all invoice lines: {candidates[0]['po_number']} (suggestion only, not applied)."
        elif len(candidates) > 1:
            hint = f" Several open POs fit the lines ({', '.join(c['po_number'] for c in candidates)}); not guessing."
        checks.append(Check(id="po_reference", category="po_match", name="Purchase order reference", status=C.FAIL,
                            detail="The invoice does not reference a purchase order." + hint,
                            expected="One PO number", actual="none",
                            next_action="Ask the vendor for the PO number, or have a reviewer assign the correct PO and re-validate."))
    elif len(refs) > 1:
        checks.append(Check(id="po_reference", category="po_match", name="Purchase order reference", status=C.FAIL,
                            detail=f"The invoice references multiple POs ({', '.join(refs)}). The correct PO cannot be chosen automatically.",
                            actual=", ".join(refs), evidence=inv.po_evidence,
                            next_action="A reviewer must decide which PO this invoice belongs to."))
    else:
        po = repo.get_po(conn, refs[0])
        if not po:
            checks.append(Check(id="po_reference", category="po_match", name="Purchase order reference", status=C.FAIL,
                                detail=f"{refs[0]} does not exist in the procurement system.", actual=refs[0],
                                evidence=inv.po_evidence, next_action="Verify the PO number with the vendor (possible typo)."))
        else:
            checks.append(Check(id="po_reference", category="po_match", name="Purchase order reference", status=C.PASS,
                                detail=f"{po['po_number']} found ({po['description']}).", actual=refs[0], evidence=inv.po_evidence))

    line_matches: list[dict] = []
    if po:
        # owner
        if vendor and vendor["id"] != po["vendor_id"]:
            owner = repo.get_vendor(conn, po["vendor_id"])
            checks.append(Check(id="po_vendor", category="po_match", name="PO belongs to invoicing vendor", status=C.FAIL,
                                detail=f"{po['po_number']} was issued to {owner['name']}, but the invoice is from {vendor['name']}.",
                                expected=owner["name"], actual=vendor["name"],
                                next_action="Possible billing error or fraud attempt. Confirm with the vendor and the PO owner."))
        elif vendor:
            checks.append(Check(id="po_vendor", category="po_match", name="PO belongs to invoicing vendor", status=C.PASS,
                                detail=f"{po['po_number']} is owned by {vendor['name']}."))
        else:
            checks.append(Check(id="po_vendor", category="po_match", name="PO belongs to invoicing vendor", status=C.SKIPPED,
                                detail="Vendor not identified, so PO ownership cannot be confirmed."))
        # status
        if po["status"] in OPEN_PO_STATUSES:
            checks.append(Check(id="po_status", category="po_match", name="PO status", status=C.PASS,
                                detail=f"PO is {po['status']}.", actual=po["status"], expected="OPEN / PARTIALLY_BILLED"))
        else:
            checks.append(Check(id="po_status", category="po_match", name="PO status", status=C.FAIL,
                                detail=f"PO is {po['status']}; it cannot receive new invoices.", actual=po["status"],
                                expected="OPEN / PARTIALLY_BILLED",
                                next_action="Procurement must reopen or replace the PO before payment."))
        # currency
        cur = inv.currency.value
        if not cur:
            checks.append(Check(id="po_currency", category="po_match", name="Currency matches PO", status=C.SKIPPED,
                                detail="Invoice currency unknown."))
        elif cur != po["currency"]:
            checks.append(Check(id="po_currency", category="po_match", name="Currency matches PO", status=C.FAIL,
                                detail=f"Invoice is in {cur} but the PO is in {po['currency']}.", expected=po["currency"], actual=cur,
                                evidence=inv.currency.evidence, next_action="Ask the vendor to reissue in the PO currency."))
        else:
            checks.append(Check(id="po_currency", category="po_match", name="Currency matches PO", status=C.PASS,
                                detail=f"Both are {cur}.", expected=po["currency"], actual=cur))
        # lines
        if not inv.line_items:
            checks.append(Check(id="line_match", category="po_match", name="Invoice lines match PO lines", status=C.FAIL,
                                detail="No line items were extracted, so the invoice cannot be compared to the PO.",
                                next_action="Review the document manually."))
        else:
            line_matches = match_lines(inv.line_items, po["lines"])
            bad = [(inv.line_items[m["inv_index"]].description, m["score"]) for m in line_matches if m["score"] < LINE_MATCH]
            if bad:
                checks.append(Check(id="line_match", category="po_match", name="Invoice lines match PO lines", status=C.FAIL,
                                    detail="Lines not found on the PO: " + "; ".join(f"'{d}'" for d, _ in bad) + ".",
                                    expected="; ".join(l["description"] for l in po["lines"]),
                                    next_action="Confirm whether these items were ordered; request a PO amendment if so."))
            else:
                checks.append(Check(id="line_match", category="po_match", name="Invoice lines match PO lines", status=C.PASS,
                                    detail=f"All {len(line_matches)} invoice line(s) map to PO lines."))
    else:
        for cid, nm in (("po_vendor", "PO belongs to invoicing vendor"), ("po_status", "PO status"),
                        ("po_currency", "Currency matches PO"), ("line_match", "Invoice lines match PO lines")):
            checks.append(Check(id=cid, category="po_match", name=nm, status=C.SKIPPED, detail="No single valid PO was matched."))
    return {"checks": checks, "vendor": vendor, "po": po, "line_matches": line_matches, "candidates": candidates}
