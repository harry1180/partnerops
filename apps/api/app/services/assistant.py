"""AI PartnerOps assistant (Phase 5) — deterministic retrieval, not a chatbot.

Design (charter rules implemented literally):
- Retrieval + deterministic analytics only. A small intent router maps the
  question to a read-only tool; every tool computes its answer live from the
  caller's authorized data. There is no language model in the path, so there
  is nothing that can invent a charge, a saving, or an explanation.
- Answers cite internal records (entity types + ids). If a tool cannot find
  the data it refuses honestly with a reason.
- Permission enforcement inside the router: margin-bearing intents require
  margin.view, customer-safe drafts never read provider cost, etc. Refusals
  are audited like answers.
- Every query — answered or refused — writes an AIQueryAudit row (the
  charter's AIQueryAudit entity), with sensitive=True flagged and audited
  separately via action 'assistant.query_sensitive'.
- Write actions: the assistant has none. Read-only tools by default; any
  future write must go through approvals (none implemented here).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_org_scope
from app.models.assistant import AIQueryAudit
from app.models.benefits import Credit
from app.models.billing_core import Customer
from app.models.cost import CanonicalCostRecord
from app.models.finops import CostAnomaly, Recommendation
from app.models.invoices import Invoice, InvoiceLine
from app.models.reconciliation import ReconciliationException
from app.services.authz import RequestPrincipal

MODE = "deterministic_demo"


@dataclass
class AssistantAnswer:
    intent: str
    mode: str = MODE
    text: str = ""
    facts: list[dict] = field(default_factory=list)
    estimates: list[dict] = field(default_factory=list)  # clearly separated
    citations: list[dict] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    sensitive: bool = False
    customer_safe: bool = True  # payload itself contains no partner internals
    tools_used: list[str] = field(default_factory=list)
    latency_ms: int = 0


def _money(x: Decimal | None) -> str:
    return f"${Decimal(x or 0):,.2f}"


async def _resolve_customer(session: AsyncSession, principal: RequestPrincipal,
                            q: str) -> Customer | None:
    """Name-match from the question within the caller's scope."""
    root = principal.scope_prefixes[0]
    customers = list((
        await session.execute(
            select(Customer).where(Customer.org_path.like(root + "%"),
                                   Customer.deleted_at.is_(None))
        )
    ).scalars())
    ql = q.lower()
    best = None
    for c in customers:
        tokens = {w.strip(",.\"") for w in c.display_name.lower().split()} - {""}
        name_ok = c.display_name.lower() in ql or bool(tokens & set(ql.split()))
        # longest matching name wins (Acme before Acme-adjacent matches)
        if name_ok and (best is None or len(c.display_name) > len(best.display_name)):
            best = c
    return best


async def _invoice_for(session: AsyncSession, principal: RequestPrincipal, q: str,
                       customer: Customer | None) -> Invoice | None:
    root = principal.scope_prefixes[0]
    m = re.search(r"\b([A-Z]{2,4}-\d{6}-\d{4}|[A-Z]{2,3}-\d{6}-\d{4})\b", q.upper())
    stmt = select(Invoice).where(Invoice.org_path.like(root + "%"),
                                 Invoice.deleted_at.is_(None))
    if m:
        stmt = stmt.where(Invoice.invoice_number == m.group(1))
        inv = (await session.execute(stmt)).scalars().first()
        if inv:
            return inv
    if customer is None:
        return None
    stmt = stmt.where(Invoice.customer_id == customer.id)
    return (await session.execute(stmt.order_by(Invoice.period_start.desc()).limit(1))
            ).scalars().first()


# ---------------- tools ----------------

async def tool_invoice_change(session, principal, q, customer):
    inv = await _invoice_for(session, principal, q, customer)
    if inv is None:
        return AssistantAnswer(intent="invoice_change", refused=True,
                               refusal_reason="no invoice matched for a customer in your scope")
    prev = (
        await session.execute(
            select(Invoice).where(Invoice.customer_id == inv.customer_id,
                                  Invoice.period_end <= inv.period_start,
                                  Invoice.deleted_at.is_(None))
            .order_by(Invoice.period_start.desc()).limit(1))
    ).scalars().first()
    if prev is None:
        return AssistantAnswer(intent="invoice_change", refused=True,
                               refusal_reason="no prior invoice to compare against")
    delta = Decimal(inv.total) - Decimal(prev.total)
    # top drivers: compare customer-visible line amounts grouped by description
    cur_lines = (await session.execute(
        select(InvoiceLine.description, func.sum(InvoiceLine.amount))
        .where(InvoiceLine.invoice_id == inv.id, InvoiceLine.customer_visible)
        .group_by("description").order_by(func.sum(InvoiceLine.amount).desc())
    )).all()
    prev_lines = dict((await session.execute(
        select(InvoiceLine.description, func.sum(InvoiceLine.amount))
        .where(InvoiceLine.invoice_id == prev.id, InvoiceLine.customer_visible)
        .group_by("description")
    )).all())
    drivers = []
    for desc, amt in cur_lines:
        diff = Decimal(amt or 0) - Decimal(prev_lines.get(desc, 0) or 0)
        if diff != 0:
            drivers.append((desc, diff))
    drivers.sort(key=lambda d: abs(d[1]), reverse=True)
    line_changes = [{"line": d, "change": str(c)} for d, c in drivers[:5]]
    facts = [
        {"fact": "invoice total", "invoice": inv.invoice_number,
         "period": f"{inv.period_start:%Y-%m}", "amount": str(inv.total)},
        {"fact": "previous invoice", "invoice": prev.invoice_number,
         "period": f"{prev.period_start:%Y-%m}", "amount": str(prev.total)},
        {"fact": "change", "amount": str(delta),
         "pct": str(((delta / Decimal(prev.total)) * 100).quantize(Decimal("0.1"))
                    if prev.total else "n/a")},
        *[{"fact": "line_change", **lc} for lc in line_changes],
    ]
    arrow = "increased" if delta >= 0 else "decreased"
    text = (f"{inv.invoice_number} ({inv.period_start:%B %Y}) {arrow} by "
            f"{_money(abs(delta))} vs {prev.invoice_number}. "
            + ("Largest line-level changes: "
               + ", ".join(f"{d} {'+' if c >= 0 else ''}{_money(c)}" for d, c in drivers[:3])
               if drivers else "No customer-visible line moved (credits/fees changed)."))
    return AssistantAnswer(
        intent="invoice_change", text=text,
        facts=facts, estimates=[],
        citations=[{"type": "invoice", "id": str(inv.id), "ref": inv.invoice_number},
                   {"type": "invoice", "id": str(prev.id), "ref": prev.invoice_number}],
        customer_safe=True,
        tools_used=["invoices.compare"],
    )


async def tool_rules_on_invoice(session, principal, q, customer):
    inv = await _invoice_for(session, principal, q, customer)
    if inv is None:
        return AssistantAnswer(intent="rules_on_invoice", refused=True,
                               refusal_reason="no invoice matched")
    lines = list((await session.execute(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
    )).scalars())
    rule_ids: set[str] = set()
    for ln in lines:
        for rv in ln.rule_version_ids or []:
            rule_ids.add(str(rv))
    names: list[dict] = []
    if rule_ids:
        from uuid import UUID as _U

        from app.models.contracts import BillingRuleVersion
        rows = (await session.execute(
            select(BillingRuleVersion.id, BillingRuleVersion.version_number)
            .where(BillingRuleVersion.id.in_([_U(r) for r in rule_ids])))).all()
        names = [{"rule_version_id": str(r), "version": int(v)} for r, v in rows]
    text = (f"{len(lines)} lines; {len(rule_ids)} distinct billing-rule versions "
            f"participated in the pricing run behind {inv.invoice_number}.")
    return AssistantAnswer(
        intent="rules_on_invoice", text=text,
        facts=names, citations=[{"type": "invoice", "id": str(inv.id), "ref": inv.invoice_number}],
        customer_safe=True, tools_used=["invoice_lines.rule_versions"],
    )


async def tool_lineage(session, principal, q, customer):
    inv = await _invoice_for(session, principal, q, customer)
    if inv is None:
        return AssistantAnswer(intent="lineage", refused=True,
                               refusal_reason="no invoice matched")
    nums = re.findall(r"(?:line|item)\s*#?\s*(\d+)", q.lower())
    ln_number = int(nums[0]) if nums else 1
    ln = (await session.execute(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id,
                                  InvoiceLine.line_number == ln_number)
    )).scalars().first()
    if ln is None:
        return AssistantAnswer(intent="lineage", refused=True,
                               refusal_reason=f"line {ln_number} not on invoice")
    src_ids = list(ln.source_record_ids or [])[:50]
    src_rows = (await session.execute(
        select(CanonicalCostRecord.source_record_id, CanonicalCostRecord.provider_code,
               CanonicalCostRecord.service, CanonicalCostRecord.provider_billed)
        .where(CanonicalCostRecord.source_record_id.in_(src_ids),
               CanonicalCostRecord.org_path.like(principal.scope_prefixes[0] + "%"))
    )).all()
    # partner-only enrichment: provider billed amounts never join a
    # customer-role answer even though the line itself was customer-visible
    partner_view = principal.can("margin.view") and not _is_customer(principal)
    facts = [{"source_record_id": s, "provider": p, "service": svc,
              **({"provider_billed": str(a)} if partner_view else {})}
             for s, p, svc, a in src_rows]
    return AssistantAnswer(
        intent="lineage",
        text=(f"Line {ln_number} ({ln.description}) traces to {len(src_rows)} source "
              f"billing records via its pricing run item."),
        facts=facts,
        citations=[{"type": "invoice_line", "id": str(ln.id), "ref": ln.description}],
        customer_safe=True,  # amounts only present for partner callers, see above
        tools_used=["invoice_line.lineage"],
    )


def _is_customer(principal: RequestPrincipal) -> bool:
    return principal.org_kind == "customer"


async def tool_margin_below(session, principal, q, customer):
    if not principal.can("margin.view"):
        return AssistantAnswer(intent="margin_below", refused=True,
                               refusal_reason="requires margin.view", sensitive=True)
    root = principal.scope_prefixes[0]
    rows = (await session.execute(
        select(Invoice.customer_id, func.sum(Invoice.total).label("rev"),
               func.sum(Invoice.provider_cost_total).label("cost"))
        .where(Invoice.org_path.like(root + "%"), Invoice.deleted_at.is_(None),
               Invoice.status.notin_(("voided", "draft", "calculated", "under_review")))
        .group_by("customer_id")
    )).all()
    out = []
    for cid, rev, cost in rows:
        rev, cost = Decimal(rev or 0), Decimal(cost or 0)
        if rev <= 0:
            continue
        margin = (rev - cost) / rev * 100
        cust = await session.get(Customer, cid)
        target = Decimal(cust.target_margin_pct) if cust and cust.target_margin_pct else None
        if target is None or margin < target:
            out.append({"customer": cust.display_name if cust else str(cid),
                        "margin_pct": str(margin.quantize(Decimal("0.1"))),
                        "target_pct": str(target) if target is not None else None})
    text = (f"{len(out)} customers below their target margin in issued/exported/"
            f"paid invoice history.")
    return AssistantAnswer(intent="margin_below", text=text, facts=out,
                           sensitive=True, customer_safe=False,
                           citations=[{"type": "invoice", "scope": root}][:1] or [],
                           tools_used=["invoices.margin_aggregate"])


async def tool_unbilled(session, principal, q, customer):
    root = principal.scope_prefixes[0]
    latest = (await session.execute(
        select(func.max(CanonicalCostRecord.billing_period_start))
        .where(CanonicalCostRecord.org_path.like(root + "%"))
    )).scalar_one()
    if latest is None:
        return AssistantAnswer(intent="unbilled", refused=True,
                               refusal_reason="no cost data ingested yet")
    unbilled = (await session.execute(
        select(CanonicalCostRecord.customer_id,
               func.sum(CanonicalCostRecord.provider_billed))
        .where(CanonicalCostRecord.org_path.like(root + "%"),
               CanonicalCostRecord.billing_period_start == latest,
               CanonicalCostRecord.line_item_type == "usage")
        .group_by("customer_id")
    )).all()
    out = []
    for cid, amt in unbilled:
        if cid is None:
            out.append({"account": "unmapped accounts", "amount": str(amt)})
            continue
        issued = (await session.execute(
            select(func.count(Invoice.id)).where(
                Invoice.customer_id == cid,
                Invoice.period_start == latest,
                Invoice.status.in_(("issued", "exported", "paid_or_settled")))
        )).scalar_one()
        if issued == 0:
            cust = await session.get(Customer, cid)
            out.append({"account": cust.display_name if cust else str(cid),
                        "amount": str(amt)})
    text = (f"{len(out)} customers (or the unmapped pool) have usage in "
            f"{str(latest)[:7]} with no issued invoice.")
    return AssistantAnswer(intent="unbilled", text=text, facts=out,
                           customer_safe=False, sensitive=not _is_customer(principal),
                           citations=[{"type": "canonical_cost_records",
                                       "period": str(latest)[:7]}],
                           tools_used=["cost.usage_vs_invoices"])


async def tool_unallocated_credits(session, principal, q, customer):
    root = principal.scope_prefixes[0]
    rows = list((await session.execute(
        select(Credit).where(Credit.org_path.like(root + "%"),
                             Credit.allocation_status == "unallocated",
                             Credit.deleted_at.is_(None))
    )).scalars())
    out = [{"credit_id": str(c.id), "kind": c.kind, "name": c.display_name,
            "amount": str(c.amount_total),
            "provider": c.provider_code, "expires_at":
            str(c.expires_at)[:10] if c.expires_at else None} for c in rows]
    text = (f"{len(out)} credits await allocation (total "
            f"{_money(sum((Decimal(c.amount_total or 0) for c in rows), Decimal('0')))}) — "
            "allocate via Credits & Discounts.")
    return AssistantAnswer(intent="unallocated_credits", text=text, facts=out,
                           citations=[{"type": "credit", "id": o["credit_id"]} for o in out[:10]],
                           customer_safe=False, tools_used=["credits.unallocated"])


async def tool_recon_why(session, principal, q, customer):
    root = principal.scope_prefixes[0]
    rows = list((await session.execute(
        select(ReconciliationException).where(
            ReconciliationException.org_path.like(root + "%"),
            ReconciliationException.status == "open")
        .order_by(ReconciliationException.created_at.desc()).limit(8)
    )).scalars())
    if not rows:
        return AssistantAnswer(intent="recon_why", text="No open reconciliation exceptions in your scope.",
                               facts=[], customer_safe=False,
                               citations=[], tools_used=["recon.open_exceptions"])
    out = [{"type": r.exc_type, "delta": str(r.amount_delta), "material": r.materiality,
            "explanation": r.explanation[:160], "run_id": str(r.run_id)} for r in rows]
    return AssistantAnswer(
        intent="recon_why",
        text=(f"{len(out)} open exceptions. Most recent explain themselves like: "
              + out[0]["explanation"]),
        facts=out, customer_safe=False, sensitive=True,
        citations=[{"type": "reconciliation_run", "id": r["run_id"]} for r in out],
        tools_used=["recon.open_exceptions"])


async def tool_top_savings(session, principal, q, customer):
    root = principal.scope_prefixes[0]
    rows = list((await session.execute(
        select(Recommendation).where(
            Recommendation.org_path.like(root + "%"),
            Recommendation.realized_savings.isnot(None))
        .order_by(Recommendation.realized_savings.desc()).limit(5)
    )).scalars())
    out = [{"recommendation_id": str(r.id), "kind": r.kind, "title": r.title,
            "realized_monthly": str(r.realized_savings),
            "basis": r.realized_basis} for r in rows]
    text = (f"{len(out)} recommendations have MEASURED savings." if out else
            "No measured savings yet — realization requires billing data after the "
            "accepted decision month. Nothing here is projected.")
    return AssistantAnswer(intent="top_savings", text=text, facts=out,
                           estimates=[], customer_safe=False,
                           citations=[{"type": "recommendation", "id": o["recommendation_id"]}
                                      for o in out],
                           tools_used=["recommendations.realized"])


async def tool_summarize_anomalies(session, principal, q, customer):
    root = principal.scope_prefixes[0]
    cust = customer
    if cust is None and not principal.can("margin.view"):
        cust = None  # customer roles only ever see their own scope below
    stmt = select(CostAnomaly).where(CostAnomaly.org_path.like(root + "%"))
    if cust is not None:
        stmt = stmt.where(CostAnomaly.customer_id == cust.id)
    rows = list((await session.execute(
        stmt.order_by(CostAnomaly.detected_on.desc()).limit(8))).scalars())
    out = [{"month": str(a.detected_on)[:7], "kind": a.kind, "service": a.service,
            "observed": str(a.observed_amount), "baseline": str(a.baseline_amount),
            "status": a.status, "anomaly_id": str(a.id)} for a in rows]
    text = (f"{len(out)} anomalies in scope; latest first." if out
            else "No anomalies detected yet in your scope — run the detection pass first.")
    return AssistantAnswer(intent="summarize_anomalies", text=text, facts=out,
                           customer_safe=False,
                           citations=[{"type": "cost_anomaly", "id": o["anomaly_id"]}
                                      for o in out],
                           tools_used=["anomalies.recent"])


async def tool_draft_customer_safe(session, principal, q, customer):
    inv = await _invoice_for(session, principal, q, customer)
    if inv is None:
        return AssistantAnswer(intent="draft_customer_safe", refused=True,
                               refusal_reason="no invoice matched")
    prev = (await session.execute(
        select(Invoice).where(Invoice.customer_id == inv.customer_id,
                              Invoice.period_end <= inv.period_start,
                              Invoice.deleted_at.is_(None))
        .order_by(Invoice.period_start.desc()).limit(1))).scalars().first()
    lines = list((await session.execute(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id,
                                  InvoiceLine.customer_visible)
        .order_by(InvoiceLine.amount.desc()).limit(6))).scalars())
    delta = (Decimal(inv.total) - Decimal(prev.total)) if prev else Decimal("0")
    safe = ("\n".join([
        f"Subject: {inv.invoice_number} ({inv.period_start:%B %Y}) — summary of changes",
        "",
        f"Hello, your {inv.period_start:%B %Y} statement totals {_money(Decimal(inv.total))} "
        + (f", {('+' if delta >= 0 else '−')}{_money(abs(delta))} vs the prior month" if prev else "")
        + ".",
        "What drove it:",
        *[f"  • {ln.description}: {_money(Decimal(ln.amount))}" for ln in lines],
        "",
        "Questions or a disputed line? Reply here or open a dispute from your portal.",
        "",
        "— [partner signature]",
    ]))
    return AssistantAnswer(
        intent="draft_customer_safe",
        text="Draft below — no provider cost, no margins, only customer-visible lines. "
             "Review before sending.",
        facts=[{"draft": safe}],
        citations=[{"type": "invoice", "id": str(inv.id), "ref": inv.invoice_number}],
        customer_safe=True, tools_used=["invoice_lines.customer_visible"],
    )


INTENTS: list[tuple[re.Pattern, object]] = [
    (re.compile(r"draft|customer[- ]safe explanation", re.I), tool_draft_customer_safe),
    (re.compile(r"why.*(invoice|charge|bill)|went up|increase.*invoice|invoice.*chang"
                r"e", re.I), tool_invoice_change),
    (re.compile(r"\brules?\b.*(invoice|affect|apply)", re.I), tool_rules_on_invoice),
    (re.compile(r"source record|lineage|behind this line", re.I), tool_lineage),
    (re.compile(r"below.*margin|unprofitable|margin.*(below|target)", re.I), tool_margin_below),
    (re.compile(r"unbilled|un-?invoiced|missing from invoice", re.I), tool_unbilled),
    (re.compile(r"unallocated credit|credit.*\b(not|un|still)\b.*alloc", re.I),
     tool_unallocated_credits),
    (re.compile(r"reconcil|discrepan|difference", re.I), tool_recon_why),
    (re.compile(r"(highest|top|verified).*(saving)|saving.*(verified|highest)", re.I), tool_top_savings),
    (re.compile(r"anomal", re.I), tool_summarize_anomalies),
]


async def ask(session: AsyncSession, principal: RequestPrincipal, question: str,
              correlation_id: str | None = None) -> AssistantAnswer:
    """Route question → tool, run read-only, audit everything."""
    t0 = time.perf_counter()
    await set_org_scope(session, principal.scope_prefixes[0])
    customer = await _resolve_customer(session, principal, question)
    answer: AssistantAnswer | None = None
    for pattern, tool in INTENTS:
        if pattern.search(question):
            answer = await tool(session, principal, question, customer)  # type: ignore[operator]
            break
    if answer is None:
        answer = AssistantAnswer(intent="none", refused=True,
                                 refusal_reason="no read-only tool matches this question")
    answer.latency_ms = int((time.perf_counter() - t0) * 1000)
    answer.tools_used = answer.tools_used or ["router.none"]
    # audit (the AIQueryAudit entity; sensitive queries flagged separately)
    audit = AIQueryAudit(
        asked_by=principal.user_id, org_id=principal.org_id,
        org_path=principal.org_path, question=question[:2000],
        intent=answer.intent, mode=MODE, tools_used=answer.tools_used,
        citations=answer.citations, exposed_customer_safe=answer.customer_safe,
        refused=answer.refused, refusal_reason=answer.refusal_reason,
        sensitive=answer.sensitive,
        answer_summary=(answer.text if not answer.refused
                        else f"refused: {answer.refusal_reason}")[:500],
        latency_ms=answer.latency_ms,
    )
    session.add(audit)
    await session.commit()
    return answer
