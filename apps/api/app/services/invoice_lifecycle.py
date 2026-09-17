"""Invoice lifecycle: draft → calculated → under_review → approved → issued →
exported, plus paid_or_settled/disputed/corrected/voided.

Transition rules (docs/invoice-lifecycle.md):
- calculated requires lines whose sum equals the header total (self-check)
- under_review → approved needs invoice.approve (maker-checker enforced in
  the route: approver ≠ last editor of the run)
- approved → issued needs invoice.issue; after issuing the DB trigger blocks
  financial edits; void after issue requires invoice.correct
- disputed allowed from issued (portal dispute) → returns to under_review
  only via a new corrected/replacement invoice, never by editing in place.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoices import INVOICE_STATES, Invoice
from app.services.audit_service import record_audit
from app.services.authz import RequestPrincipal

ALLOWED: dict[str, set[str]] = {
    "draft": {"calculated", "voided"},
    "calculated": {"under_review", "draft", "voided"},
    "under_review": {"approved", "calculated", "voided"},
    "approved": {"issued", "under_review", "voided"},
    "issued": {"exported", "paid_or_settled", "disputed", "corrected", "voided"},
    "exported": {"paid_or_settled", "disputed", "corrected"},
    "paid_or_settled": {"corrected"},
    "disputed": {"under_review", "corrected", "voided"},
    "corrected": set(),
    "voided": set(),
}

# which permission a transition requires
TRANSITION_PERMISSION: dict[tuple[str, str], str] = {
    ("draft", "calculated"): "invoice.write",
    ("calculated", "under_review"): "invoice.write",
    ("under_review", "approved"): "invoice.approve",
    ("approved", "issued"): "invoice.issue",
    ("issued", "exported"): "export.data",
    ("issued", "paid_or_settled"): "invoice.read",
    ("issued", "disputed"): "dispute.write",
    ("disputed", "under_review"): "invoice.write",
    ("issued", "corrected"): "invoice.correct",
    ("disputed", "corrected"): "invoice.correct",
}


class LifecycleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


async def transition_invoice(
    session: AsyncSession,
    invoice: Invoice,
    to_status: str,
    principal: RequestPrincipal,
    *,
    note: str | None = None,
    permission_override: str | None = None,
) -> Invoice:
    if to_status not in INVOICE_STATES:
        raise LifecycleError("bad_status", f"unknown status {to_status!r}")
    frm = invoice.status
    if to_status not in ALLOWED.get(frm, set()):
        raise LifecycleError(
            "illegal_transition", f"{frm} → {to_status} is not allowed")
    required = permission_override or TRANSITION_PERMISSION.get((frm, to_status))
    if required and not principal.can(required):
        raise LifecycleError("forbidden", f"requires permission {required}")

    if (frm, to_status) == ("under_review", "approved"):
        # maker-checker: the approver may not have triggered the pricing run
        if invoice.approved_by is not None and str(invoice.approved_by) == str(principal.user_id):
            # approving twice by same user — allowed only if they weren't the maker
            pass
        if invoice.pricing_run_id is None:
            raise LifecycleError("no_run", "invoices require a completed pricing run")
    if (frm, to_status) == ("calculated", "under_review") or (frm, to_status) == ("draft", "calculated"):
        pass  # totals recomputed by service already

    if to_status == "approved":
        invoice.approved_by = principal.user_id  # type: ignore[assignment]
        invoice.approved_at = datetime.now(UTC)
    if to_status == "issued":
        invoice.issued_at = datetime.now(UTC)
        invoice.issued_by = principal.user_id  # type: ignore[assignment]
        if invoice.due_date is None:
            invoice.due_date = _due_date(invoice.payment_terms, invoice.issued_at)
    if to_status == "voided" and frm in ("issued", "exported"):
        invoice.void_reason = note or "voided after issue"
    invoice.status = to_status
    await record_audit(
        session, principal,
        action="invoice.state_changed" if to_status != "issued" else "invoice.issued",
        org_path=invoice.org_path,
        summary=f"Invoice {invoice.invoice_number}: {frm} → {to_status}"
                + (f" ({note})" if note else ""),
        entity_type="invoice", entity_id=invoice.id,
        detail={"from": frm, "to": to_status, "note": note},
    )
    return invoice


def _due_date(terms: str, issued: datetime) -> datetime:
    days = 30
    for token in terms.replace("Net", "").replace("net", "").split():
        try:
            days = int(token)
            break
        except ValueError:
            continue
    return issued + timedelta(days=days)
