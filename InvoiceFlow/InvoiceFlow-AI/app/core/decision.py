"""Stage 9 - the decision engine. Deterministic: a pure function of the check results."""
from __future__ import annotations

from app.core.models import Check, CheckStatus as C, Decision, RunResult

BAD = {C.FAIL, C.UNCERTAIN}


def decide(checks: list[Check]) -> Decision:
    if any(c.status == C.REJECT for c in checks):
        return Decision.REJECTED
    if any(c.status in BAD for c in checks):
        return Decision.REVIEW
    return Decision.APPROVED


def explain(res: RunResult) -> None:
    """Fills res.summary_line, res.explanation (markdown) and res.next_actions from res.checks."""
    checks = res.checks
    passed = [c for c in checks if c.status == C.PASS]
    noted = [c for c in checks if c.status == C.WARN]
    rejects = [c for c in checks if c.status == C.REJECT]
    bad = [c for c in checks if c.status in BAD]
    skipped = [c for c in checks if c.status == C.SKIPPED]
    d = res.decision

    if d == Decision.APPROVED:
        res.summary_line = f"Approved: {len(passed) + len(noted)} checks passed" + (f" ({len(noted)} with notes)" if noted else "") + "."
        why = ("Every required field was found with sufficient confidence, the vendor and purchase order are valid and "
               "agree with each other, the invoice arithmetic reconciles, prices and balance are within tolerance and no "
               "duplicate exists. No rule requires a human decision.")
        res.next_actions = ["Release to the normal payment run. This system records the approval and has not made any payment."]
    elif d == Decision.REJECTED:
        res.summary_line = "Rejected: " + "; ".join(c.name.lower() for c in rejects) + "."
        why = ("An explicit rejection rule fired: " + " ".join(c.detail for c in rejects)
               + " Rejection is reserved for confirmed duplicates of approved invoices and blocked vendors.")
        res.next_actions = list(dict.fromkeys(c.next_action for c in rejects if c.next_action))
    else:
        res.summary_line = f"Needs review: {len(bad)} issue(s) - " + "; ".join(c.name.lower() for c in bad[:3]) + ("..." if len(bad) > 3 else ".")
        why = ("The invoice cannot be approved automatically because at least one check failed or could not be trusted. "
               "Nothing here is a confirmed duplicate or other rejection condition, so a person should decide.")
        res.next_actions = list(dict.fromkeys(c.next_action for c in bad if c.next_action))

    lines = [f"**Decision: {d.value}.** {why}", "",
             f"**What was checked:** {len(checks)} checks across file, extraction, data, PO matching, financial and duplicate rules "
             f"({len(passed)} passed, {len(noted)} passed with a note, {len(bad) + len(rejects)} failed, {len(skipped)} skipped)."]
    if passed or noted:
        lines += ["", "**What passed:**"] + [f"- {c.name}" + (f" (note: {c.detail})" if c.status == C.WARN else "") for c in passed + noted]
    if bad or rejects:
        lines += ["", "**What failed:**"] + [f"- {c.name}: {c.detail}" for c in rejects + bad]
    if skipped:
        lines += ["", "**Not evaluated:**"] + [f"- {c.name}: {c.detail}" for c in skipped]
    if res.next_actions:
        lines += ["", "**Next action:**"] + [f"- {a}" for a in res.next_actions]
    res.explanation = "\n".join(lines)
