"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import {
  fetchDq, fetchExceptions, resolveException, runReconciliation,
  type DqSummary, type ReconException, type ReconPage,
} from "@/lib/billing-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, Field,
  Input, MoneyCell, PageHeader, Select, Spinner, StatusPill, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const PERIODS = [
  { label: "June 2026", start: "2026-06-01T00:00:00+00:00", end: "2026-07-01T00:00:00+00:00" },
  { label: "July 2026", start: "2026-07-01T00:00:00+00:00", end: "2026-08-01T00:00:00+00:00" },
  { label: "August 2026", start: "2026-08-01T00:00:00+00:00", end: "2026-09-01T00:00:00+00:00" },
];

export default function ReconciliationPage() {
  const { me } = useApp();
  const canResolve = me?.permissions.includes("recon.resolve") ?? false;
  const canRun = me?.permissions.includes("recon.read") ?? false;
  const [periodIdx, setPeriodIdx] = useState(0);
  const [statusFilter, setStatusFilter] = useState("open");
  const [exceptions, setExceptions] = useState<ReconPage | null>(null);
  const [dq, setDq] = useState<DqSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState<{ exceptions_open: number; material_open: number } | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchExceptions(1, statusFilter || undefined).then(setExceptions).catch((e) => setError(String(e)));
    fetchDq().then(setDq).catch(() => setDq(null));
  }, [statusFilter]);
  useEffect(load, [load]);

  async function run() {
    setRunning(true); setError(null);
    try {
      const p = PERIODS[periodIdx];
      const r = await runReconciliation({ period_start: p.start, period_end: p.end });
      setLastRun({ exceptions_open: r.exceptions_open, material_open: r.material_open });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "reconciliation failed");
    } finally { setRunning(false); }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Reconciliation"
        subtitle="Three-way match: provider bill ↔ normalized cost ↔ customer invoices. Material exceptions block period close."
        actions={canRun ? <Button loading={running} onClick={run}>Run reconciliation</Button> : undefined}
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Period to reconcile">
          {(id) => (
            <Select id={id} className="w-52" value={String(periodIdx)} onChange={(e) => setPeriodIdx(Number(e.target.value))}>
              {PERIODS.map((p, i) => <option key={p.label} value={i}>{p.label}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Exception filter">
          {(id) => (
            <Select id={id} className="w-44" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="open">Open</option><option value="investigating">Investigating</option>
              <option value="resolved">Resolved</option><option value="">All</option>
            </Select>
          )}
        </Field>
        {lastRun && (
          <Badge tone={lastRun.material_open > 0 ? "negative" : "positive"}>
            {PERIODS[periodIdx].label}: {lastRun.exceptions_open} exceptions ({lastRun.material_open} material)
          </Badge>
        )}
      </div>

      {error && <Card className="mb-4 p-4"><ErrorState detail={error} onRetry={load} /></Card>}

      {dq && (
        <Card className="mb-4">
          <CardHeader><CardTitle>Data quality</CardTitle></CardHeader>
          <CardBody>
            <div className="grid gap-3 text-sm sm:grid-cols-4">
              <div className="rounded-lg border border-ink-200 px-3 py-2">
                <div className="text-xs text-ink-500">Billing periods ingested</div>
                <div className="font-display text-lg">{dq.billing_periods_present.length}</div>
                {dq.missing_parsed_files_periods.length > 0 && <div className="text-xs text-negative">{dq.missing_parsed_files_periods.length} missing files</div>}
              </div>
              <div className="rounded-lg border border-ink-200 px-3 py-2">
                <div className="text-xs text-ink-500">Unmapped usage</div>
                <div className="font-display text-lg">{dq.unmapped_usage.length} accounts</div>
                {dq.unmapped_usage.slice(0, 2).map((u) => <div key={u.account} className="truncate text-xs text-warning">{u.account}: ${Number(u.amount).toLocaleString("en-US", { maximumFractionDigits: 0 })}</div>)}
              </div>
              <div className="rounded-lg border border-ink-200 px-3 py-2">
                <div className="text-xs text-ink-500">Quarantined rows</div>
                <div className="font-display text-lg">{Object.values(dq.quarantined_by_reason).reduce((a, b) => a + b, 0)}</div>
                {Object.entries(dq.quarantined_by_reason).map(([r, n]) => <div key={r} className="text-xs text-ink-400">{r.replace(/_/g, " ")}: {n}</div>)}
              </div>
              <div className="rounded-lg border border-ink-200 px-3 py-2">
                <div className="text-xs text-ink-500">Failed jobs</div>
                <div className={`font-display text-lg ${dq.failed_jobs.length ? "text-negative" : ""}`}>{dq.failed_jobs.length}</div>
              </div>
            </div>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>Exceptions</CardTitle></CardHeader>
        <CardBody className="p-0">
          {!exceptions ? <div className="p-5"><Spinner label="Loading exceptions" /></div> :
            exceptions.items.length === 0 ? <div className="p-5"><EmptyState title="No exceptions in this filter" hint="Open a period and run reconciliation to populate." /></div> : (
            <Table>
              <THead><TR><TH>Type</TH><TH>Material</TH><TH className="text-right">Δ</TH><TH>Explanation</TH><TH>Status</TH><TH /></TR></THead>
              <TBody>
                {exceptions.items.map((e) => (
                  <ExceptionRow key={e.id} e={e} canResolve={canResolve} onDone={load} />
                ))}
              </TBody>
            </Table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function ExceptionRow({ e, canResolve, onDone }: { e: ReconException; canResolve: boolean; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  return (
    <TR>
      <TD><Badge tone={e.severity === "critical" ? "negative" : e.severity === "medium" ? "warning" : "neutral"}>{e.type.replace(/_/g, " ")}</Badge></TD>
      <TD><Badge tone={e.materiality === "material" ? "negative" : "neutral"}>{e.materiality}</Badge></TD>
      <TD className="text-right"><MoneyCell value={e.amount_delta} /></TD>
      <TD className="max-w-sm text-xs">{e.explanation}</TD>
      <TD><StatusPill status={e.status} /></TD>
      <TD className="text-right">
        {canResolve && e.status === "open" && (
          <Button size="sm" variant="secondary" loading={busy} onClick={() => setOpen(!open)}>
            {open ? "Close" : "Resolve"}
          </Button>
        )}
        {open && (
          <div className="mt-2 flex gap-1">
            <Input className="h-8 w-40 text-xs" placeholder="resolution note" value={note} onChange={(ev) => setNote(ev.target.value)} />
            <Button size="sm" loading={busy} onClick={async () => {
              setBusy(true);
              try { await resolveException(e.id, "resolved", note || "investigated and resolved"); onDone(); }
              finally { setBusy(false); setOpen(false); }
            }}>Submit</Button>
          </div>
        )}
      </TD>
    </TR>
  );
}
