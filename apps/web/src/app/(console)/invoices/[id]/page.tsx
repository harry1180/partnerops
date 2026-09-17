"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useApp } from "@/lib/app-state";
import {
  billingDocUrl, fetchInvoice, invoiceLineage, transitionInvoice,
  type InvoiceDetail,
} from "@/lib/billing-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, ErrorState, Modal, MoneyCell,
  PageHeader, Spinner, StatusPill, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const NEXT_ACTIONS: Record<string, { to: string; label: string; permission: string; tone?: "primary" | "secondary" | "danger" }[]> = {
  calculated: [{ to: "under_review", label: "Send to review", permission: "invoice.write" }],
  under_review: [
    { to: "approved", label: "Approve", permission: "invoice.approve" },
    { to: "calculated", label: "Return to calculated", permission: "invoice.write" },
  ],
  approved: [{ to: "issued", label: "Issue invoice", permission: "invoice.issue" }],
  issued: [
    { to: "paid_or_settled", label: "Mark settled (external)", permission: "invoice.read" },
    { to: "disputed", label: "Mark disputed", permission: "dispute.write" },
  ],
};

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useApp();
  const [inv, setInv] = useState<InvoiceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lineageFor, setLineageFor] = useState<number | null>(null);
  const [lineage, setLineage] = useState<Awaited<ReturnType<typeof invoiceLineage>> | null>(null);

  const canSeeMargin = me?.permissions.includes("margin.view") ?? false;

  const load = useCallback(() => {
    setError(null);
    fetchInvoice(id).then(setInv).catch((e) => setError(String(e?.message ?? e)));
  }, [id]);
  useEffect(load, [load]);

  async function doTransition(to: string) {
    setBusy(true);
    try {
      await transitionInvoice(id, to);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "transition failed");
    } finally {
      setBusy(false);
    }
  }

  async function openLineage(lineNumber: number) {
    setLineageFor(lineNumber);
    setLineage(null);
    try {
      setLineage(await invoiceLineage(id, lineNumber));
    } catch (e) {
      setLineage(null);
      setError(e instanceof Error ? e.message : "lineage failed");
    }
  }

  if (!inv && !error) return <Spinner label="Loading invoice" />;
  if (error && !inv) return <ErrorState detail={error} onRetry={load} />;
  if (!inv) return null;

  const actions = NEXT_ACTIONS[inv.status] ?? [];

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title={inv.invoice_number}
        subtitle={<span>{new Date(inv.period_start).toLocaleDateString()} → {new Date(inv.period_end).toLocaleDateString()} · {inv.payment_terms}</span>}
        actions={
          <div className="flex flex-wrap gap-2">
            <a href={billingDocUrl(id, "pdf")}><Button variant="secondary" size="sm">Download PDF</Button></a>
            <a href={billingDocUrl(id, "csv")}><Button variant="secondary" size="sm">Download CSV</Button></a>
            {actions
              .filter((a) => me?.permissions.includes(a.permission))
              .map((a) => (
                <Button key={a.to} size="sm" loading={busy}
                        variant={a.to === "issued" ? "primary" : a.to === "disputed" ? "danger" : "secondary"}
                        onClick={() => void doTransition(a.to)}>
                  {a.label}
                </Button>
              ))}
            <StatusPill status={inv.status} />
          </div>
        }
      />

      <Card>
        <CardHeader><CardTitle>Summary</CardTitle></CardHeader>
        <CardBody>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm sm:grid-cols-3">
            {[
              ["Subtotal", inv.subtotal],
              ["Discounts", inv.discounts_total],
              ["Credits", inv.credits_total],
              ["Service fees", inv.fees_total],
              ["Adjustments", inv.adjustments_total],
              ["Taxes", inv.taxes_total],
              ["Prior-period adjustments", inv.prior_period_adjustments_total],
            ].map(([label, value]) => (
              <div key={label} className="flex justify-between border-b border-ink-100 py-1">
                <dt className="text-ink-500">{label}</dt>
                <dd className="tabular-nums">{Number(value) !== 0 ? <MoneyCell value={String(value)} currency={inv.currency} /> : <span className="text-ink-300">0.00</span>}</dd>
              </div>
            ))}
            <div className="col-span-2 flex justify-between border-t-2 border-brand-primary py-1.5 font-semibold sm:col-span-3">
              <dt>Total due</dt>
              <dd><MoneyCell value={inv.total} currency={inv.currency} /></dd>
            </div>
            {canSeeMargin && (
              <div className="col-span-2 flex justify-between py-1 sm:col-span-3">
                <dt className="text-ink-500">Provider cost / margin <Badge tone="warning">internal</Badge></dt>
                <dd className="tabular-nums text-ink-700"><MoneyCell value={inv.provider_cost_total} currency={inv.currency} /> / <MoneyCell value={inv.margin_total} currency={inv.currency} /></dd>
              </div>
            )}
          </dl>
          {inv.notes_customer && <p className="mt-3 rounded-lg bg-ink-50 px-3 py-2 text-xs text-ink-600">{inv.notes_customer}</p>}
          {canSeeMargin && inv.notes_internal && <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-ink-600">Internal: {inv.notes_internal}</p>}
        </CardBody>
      </Card>

      <Card className="mt-4">
        <CardHeader><CardTitle>Line items</CardTitle></CardHeader>
        <CardBody className="p-0">
          <Table>
            <THead><TR><TH>#</TH><TH>Description</TH><TH>Qty</TH><TH className="text-right">Amount</TH><TH /></TR></THead>
            <TBody>
              {inv.lines.map((l) => (
                <TR key={l.line_number}>
                  <TD className="tabular-nums text-ink-500">{l.line_number}</TD>
                  <TD>
                    <span className="capitalize text-ink-400">{l.kind.replace(/_/g, " ")} · </span>{l.description}
                  </TD>
                  <TD className="tabular-nums">{l.quantity ? Math.round(Number(l.quantity)).toLocaleString() : "—"}</TD>
                  <TD className="text-right"><MoneyCell value={l.amount} currency={inv.currency} /></TD>
                  <TD className="text-right">
                    {l.pricing_run_item_id && (
                      <Button size="sm" variant="ghost" onClick={() => void openLineage(l.line_number)}>
                        Trace
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardBody>
      </Card>

      {lineageFor !== null && (
        <Modal open wide title={`Calculation lineage — line ${lineageFor}`} onClose={() => setLineageFor(null)}>
          {!lineage ? <Spinner label="Loading lineage" /> : (
            <div className="space-y-3 text-sm">
              <p><span className="font-medium">{lineage.description}</span> — <MoneyCell value={lineage.amount} /></p>
              <p className="text-xs text-ink-500">Engine v{lineage.engine_version} · run #{lineage.run_number} · contract version {lineage.contract_version_id.slice(0, 8)}…</p>
              <div className="rounded-lg bg-ink-50 px-3 py-2 font-mono text-xs">{lineage.formula}</div>
              <Table>
                <THead><TR><TH>Rule</TH><TH>Input</TH><TH>Output</TH><TH>Δ</TH><TH>Formula</TH></TR></THead>
                <TBody>
                  {lineage.calculation_trace.map((t, i) => (
                    <TR key={i}>
                      <TD className="font-mono text-xs">{t.rule} v{t.v}</TD>
                      <TD className="tabular-nums text-xs">{t.input}</TD>
                      <TD className="tabular-nums text-xs">{t.output}</TD>
                      <TD className={`tabular-nums text-xs ${Number(t.delta) < 0 ? "text-negative" : ""}`}>{t.delta}</TD>
                      <TD className="text-xs text-ink-500">{t.formula}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
              <p className="text-xs text-ink-500">
                Source records behind this line: <span className="font-mono">{lineage.source_record_ids.length}</span> ids
                {lineage.source_record_ids.slice(0, 4).map((s) => (
                  <code key={s} className="ml-2 rounded bg-ink-100 px-1 py-0.5 text-[10px]">{s.slice(0, 18)}…</code>
                ))}
                {lineage.source_record_ids.length > 4 && <span className="ml-1">(+{lineage.source_record_ids.length - 4})</span>}
              </p>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
