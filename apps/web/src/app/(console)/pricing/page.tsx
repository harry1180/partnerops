"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useApp } from "@/lib/app-state";
import { fetchCustomers } from "@/lib/hooks";
import {
  createInvoice, fetchContracts, fetchRun, startPricing, type ContractRow, type PricingRun,
} from "@/lib/billing-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, ErrorState, Field,
  MoneyCell, PageHeader, Select, StatusPill, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const PERIODS = [
  { label: "June 2026", start: "2026-06-01T00:00:00+00:00", end: "2026-07-01T00:00:00+00:00" },
  { label: "July 2026", start: "2026-07-01T00:00:00+00:00", end: "2026-08-01T00:00:00+00:00" },
  { label: "August 2026", start: "2026-08-01T00:00:00+00:00", end: "2026-09-01T00:00:00+00:00" },
];

export default function PricingPage() {
  const { me } = useApp();
  const canRun = me?.permissions.includes("pricing.run") ?? false;
  const [customers, setCustomers] = useState<{ id: string; name: string }[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [contracts, setContracts] = useState<ContractRow[]>([]);
  const [versionId, setVersionId] = useState("");
  const [periodIdx, setPeriodIdx] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<PricingRun | null>(null);

  useEffect(() => {
    fetchCustomers(1).then((p) => {
      setCustomers(p.items.map((i) => ({ id: i.id, name: i.name })));
      setCustomerId((cur) => cur || (p.items[0]?.id ?? ""));
    }).catch(() => setCustomers([]));
  }, []);
  useEffect(() => {
    if (customerId) fetchContracts(customerId).then((cs) => {
      setContracts(cs);
      const active = cs.flatMap((c) => c.versions).find((v) => v.status === "active");
      setVersionId(active?.id ?? "");
    }).catch(() => setContracts([]));
  }, [customerId]);

  async function runPricing() {
    setBusy(true); setError(null); setRun(null);
    try {
      const p = PERIODS[periodIdx];
      const res = await startPricing({
        customer_id: customerId, contract_version_id: versionId,
        period_start: p.start, period_end: p.end,
      });
      setRun(await fetchRun(res.run_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "pricing failed");
    } finally { setBusy(false); }
  }

  const period = PERIODS[periodIdx];

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Pricing Runs" subtitle="Contract-aware pricing over normalized cloud costs. Reprocessing never changes history." />

      <Card>
        <CardBody>
          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Customer">
              {(id) => (
                <Select id={id} value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
                  {customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Contract version">
              {(id) => (
                <Select id={id} value={versionId} onChange={(e) => setVersionId(e.target.value)}>
                  <option value="">— active version —</option>
                  {contracts.flatMap((c) => c.versions
                    .filter((v) => v.status === "active")
                    .map((v) => <option key={v.id} value={v.id}>{c.code} v{v.version_number}</option>))}
                </Select>
              )}
            </Field>
            <Field label="Billing period">
              {(id) => (
                <Select id={id} value={String(periodIdx)} onChange={(e) => setPeriodIdx(Number(e.target.value))}>
                  {PERIODS.map((p, i) => <option key={p.label} value={i}>{p.label}</option>)}
                </Select>
              )}
            </Field>
            <div className="flex items-end">
              <Button className="w-full" loading={busy} disabled={!canRun || !customerId || !versionId} onClick={runPricing}>
                {canRun ? "Run pricing" : "No pricing permission"}
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>

      {error && <div className="mt-4"><ErrorState detail={error} /></div>}

      {run && (
        <div className="mt-6 space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Run #{run.run_number} — {period.label}</CardTitle>
                <div className="flex items-center gap-2">
                  <StatusPill status={run.status} />
                  <Badge tone="neutral">engine v{run.engine_version}</Badge>
                </div>
              </div>
            </CardHeader>
            <CardBody>
              <div className="grid gap-3 text-sm sm:grid-cols-4">
                <div><div className="text-xs text-ink-500">Provider cost (partner)</div><div className="font-display text-lg"><MoneyCell value={run.totals.provider_cost} /></div></div>
                <div><div className="text-xs text-ink-500">Customer billed</div><div className="font-display text-lg"><MoneyCell value={run.totals.customer_subtotal} /></div></div>
                <div><div className="text-xs text-ink-500">Credits</div><div className="font-display text-lg"><MoneyCell value={run.totals.credit_amount} /></div></div>
                <div><div className="text-xs text-ink-500">Gross margin</div><div className="font-display text-lg"><MoneyCell value={run.totals.margin} /></div></div>
              </div>
              {(me?.permissions.includes("invoice.write") ?? false) && (
                <div className="mt-4">
                  <CreateInvoiceButton runId={run.id} onDone={(invId, num) => { window.location.href = `/invoices/${invId}`; void num; }} />
                </div>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader><CardTitle>Lineage — {run.items.length} computed lines</CardTitle></CardHeader>
            <CardBody className="p-0">
              <Table>
                <THead><TR><TH>#</TH><TH>Kind</TH><TH>Group</TH><TH className="text-right">Provider cost</TH><TH className="text-right">Billed</TH><TH>Rules</TH></TR></THead>
                <TBody>
                  {run.items.map((i) => (
                    <TR key={i.item_number}>
                      <TD className="text-ink-500">{i.item_number}</TD>
                      <TD><Badge tone={i.line_kind === "usage" ? "neutral" : "info"}>{i.line_kind.replace(/_/g, " ")}</Badge></TD>
                      <TD className="text-xs">{i.group_label}</TD>
                      <TD className="text-right text-xs"><MoneyCell value={i.provider_cost_amount} /></TD>
                      <TD className="text-right text-xs font-medium"><MoneyCell value={i.output_amount} /></TD>
                      <TD className="text-xs text-ink-500">{i.rule_version_ids.length ? `${i.rule_version_ids.length} rule(s)` : "—"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardBody>
          </Card>

          {run.supersedes_id && (
            <p className="text-xs text-ink-500">
              This run supersedes <Link className="underline" href={`/pricing#run-${run.supersedes_id}`}>#{String(run.supersedes_id).slice(0, 8)}…</Link>; prior results remain auditable.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function CreateInvoiceButton({ runId, onDone }: { runId: string; onDone: (id: string, num: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="flex items-center gap-3">
      <Button loading={busy} onClick={async () => {
        setBusy(true); setErr(null);
        try {
          const inv = await createInvoice(runId);
          onDone(inv.id, inv.invoice_number);
        } catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
        finally { setBusy(false); }
      }}>
        Create invoice from this run
      </Button>
      {err && <span className="text-xs text-negative">{err}</span>}
    </div>
  );
}
