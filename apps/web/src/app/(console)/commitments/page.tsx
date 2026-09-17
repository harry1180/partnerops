"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import {
  createCommitment, fetchCommitments, fetchCoverage, type CommitmentRow, type Coverage,
} from "@/lib/ops-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, Field, Input,
  Modal, MoneyCell, PageHeader, Select, Spinner, StatusPill, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const KINDS = [
  "aws_reserved_instance", "aws_savings_plan", "azure_reservation", "azure_savings_plan",
  "enterprise_discount_program", "private_pricing_agreement", "volume_discount",
];

const PERIODS = [
  { label: "June 2026", start: "2026-06-01T00:00:00+00:00", end: "2026-07-01T00:00:00+00:00" },
  { label: "July 2026", start: "2026-07-01T00:00:00+00:00", end: "2026-08-01T00:00:00+00:00" },
  { label: "August 2026", start: "2026-08-01T00:00:00+00:00", end: "2026-09-01T00:00:00+00:00" },
];

export default function CommitmentsPage() {
  const { me } = useApp();
  const canWrite = me?.permissions.includes("customer.write") ?? false;
  const [rows, setRows] = useState<CommitmentRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [periodIdx, setPeriodIdx] = useState(0);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [covLoading, setCovLoading] = useState(true);

  const load = useCallback(() => {
    setError(null);
    fetchCommitments().then(setRows).catch((e) => setError(String(e?.message ?? e)));
  }, []);
  useEffect(load, [load]);

  const loadCoverage = useCallback(() => {
    setCovLoading(true);
    const p = PERIODS[periodIdx];
    fetchCoverage(p.start, p.end).then(setCoverage).catch(() => setCoverage(null)).finally(() => setCovLoading(false));
  }, [periodIdx]);
  useEffect(loadCoverage, [loadCoverage]);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Commitments"
        subtitle="Reserved Instances, Savings Plans and discount programs tracked for coverage and utilization. The platform never purchases or modifies provider commitments."
        actions={canWrite ? <Button onClick={() => setOpen(true)}>Track commitment</Button> : undefined}
      />

      <Card className="mb-4">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Coverage &amp; utilization — {PERIODS[periodIdx].label}</CardTitle>
            <Select aria-label="Coverage period" className="w-44" value={String(periodIdx)} onChange={(e) => setPeriodIdx(Number(e.target.value))}>
              {PERIODS.map((p, i) => <option key={p.label} value={i}>{p.label}</option>)}
            </Select>
          </div>
        </CardHeader>
        <CardBody>
          {covLoading ? <Spinner label="Computing coverage" /> : !coverage ? (
            <p className="text-sm text-ink-500">Coverage unavailable for this period (no usage ingested yet).</p>
          ) : (
            <div className="grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-lg border border-ink-200 px-3 py-2">
                <div className="text-xs text-ink-500">On-demand equivalent</div>
                <div className="font-display text-lg"><MoneyCell value={coverage.ondemand_equivalent} /></div>
              </div>
              <div className="rounded-lg border border-ink-200 px-3 py-2">
                <div className="text-xs text-ink-500">Provider billed</div>
                <div className="font-display text-lg"><MoneyCell value={coverage.provider_billed} /></div>
              </div>
              <div className="rounded-lg border border-ink-200 px-3 py-2">
                <div className="text-xs text-ink-500">Coverage</div>
                <div className="font-display text-lg">{coverage.coverage_pct ? `${Number(coverage.coverage_pct).toFixed(1)}%` : "—"}</div>
              </div>
              <div className="rounded-lg border border-ink-200 px-3 py-2">
                <div className="text-xs text-ink-500">Commitment savings</div>
                <div className="font-display text-lg text-positive"><MoneyCell value={coverage.commitment_savings} /></div>
              </div>
            </div>
          )}
          <p className="mt-2 text-xs text-ink-400">
            Computed from canonical records: usage whose provider-billed is below its on-demand equivalent is
            commitment-covered. No estimates.
          </p>
        </CardBody>
      </Card>

      {error ? <Card className="p-4"><ErrorState detail={error} onRetry={load} /></Card> :
        !rows ? <Spinner label="Loading commitments" /> : rows.length === 0 ? (
        <Card className="p-5"><EmptyState title="No commitments tracked" hint="Track the RIs / Savings Plans you own so coverage and allocation have a source of truth." /></Card>
      ) : (
        <Card>
          <CardHeader><CardTitle>{rows.length} commitment{rows.length === 1 ? "" : "s"}</CardTitle></CardHeader>
          <CardBody className="p-0">
            <Table>
              <THead><TR><TH>Commitment</TH><TH>Kind</TH><TH>Owner</TH><TH>Term</TH><TH className="text-right">Hourly</TH><TH>Status</TH></TR></THead>
              <TBody>
                {rows.map((c) => (
                  <TR key={c.id}>
                    <TD>
                      <span className="font-medium">{c.display_name}</span>
                      {c.external_id && <span className="ml-2 font-mono text-[10px] text-ink-400">{c.external_id}</span>}
                    </TD>
                    <TD><Badge tone="neutral">{c.kind.replace(/_/g, " ")}</Badge></TD>
                    <TD className="text-xs">{c.customer ?? <span className="text-ink-400">shared / unassigned</span>}</TD>
                    <TD className="text-xs tabular-nums">{c.start_date.slice(0, 10)} → {c.end_date ? c.end_date.slice(0, 10) : "open"}</TD>
                    <TD className="text-right text-xs">{c.hourly_commitment ? <MoneyCell value={c.hourly_commitment} currency={c.currency} /> : "—"}</TD>
                    <TD><StatusPill status={c.status} /></TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardBody>
        </Card>
      )}
      {open && <CreateCommitmentModal onClose={() => setOpen(false)} onDone={() => { setOpen(false); load(); }} />}
    </div>
  );
}

function CreateCommitmentModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [kind, setKind] = useState("aws_savings_plan");
  const [name, setName] = useState("");
  const [external, setExternal] = useState("");
  const [start, setStart] = useState("2026-06-01");
  const [end, setEnd] = useState("2027-06-01");
  const [hourly, setHourly] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await createCommitment({
        kind, display_name: name, external_id: external || null,
        start_date: `${start}T00:00:00+00:00`,
        end_date: end ? `${end}T00:00:00+00:00` : null,
        hourly_commitment: hourly || null,
      });
      onDone();
    } catch (err) { setError(err instanceof Error ? err.message : "failed"); }
    finally { setBusy(false); }
  }

  return (
    <Modal open title="Track a commitment" onClose={onClose}>
      <p className="mb-3 text-xs text-ink-500">
        Tracking only — the platform records commitments you already hold. Purchase execution is not
        implemented and will require a controlled connector.
      </p>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Kind">
          {(id) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => <option key={k} value={k}>{k.replace(/_/g, " ")}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Name" required>
          { (id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /> }
        </Field>
        <Field label="External ID (optional)">
          { (id) => <Input id={id} value={external} onChange={(e) => setExternal(e.target.value)} className="font-mono text-xs" /> }
        </Field>
        <Field label="Start" required>
          { (id) => <Input id={id} type="date" value={start} onChange={(e) => setStart(e.target.value)} required /> }
        </Field>
        <Field label="End (optional)">
          { (id) => <Input id={id} type="date" value={end} onChange={(e) => setEnd(e.target.value)} /> }
        </Field>
        <Field label="Hourly commitment (optional)">
          { (id) => <Input id={id} value={hourly} onChange={(e) => setHourly(e.target.value)} placeholder="0.50" /> }
        </Field>
        {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" loading={busy}>Track</Button></div>
      </form>
    </Modal>
  );
}
