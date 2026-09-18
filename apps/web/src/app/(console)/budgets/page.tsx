"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import { api } from "@/lib/api";
import {
  createBudget, deleteBudget, evaluateAlerts, fetchAnomalies, fetchBudgets, fetchForecast,
  reviewAnomaly, runAnomalyPass,
  type AnomalyRow, type BudgetRow, type ForecastResp,
} from "@/lib/finops-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, Field,
  Input, Modal, MoneyCell, PageHeader, Select, Spinner, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

export default function BudgetsPage() {
  const { me } = useApp();
  const canManage = me?.permissions.includes("budget.manage") ?? false;
  const canReview = me?.permissions.includes("anomaly.review") ?? false;
  const canAlert = me?.permissions.includes("alert.manage") ?? false;
  const [budgets, setBudgets] = useState<BudgetRow[] | null>(null);
  const [anomalies, setAnomalies] = useState<AnomalyRow[] | null>(null);
  const [forecast, setForecast] = useState<ForecastResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [customers, setCustomers] = useState<{ id: string; name: string }[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([fetchBudgets(), fetchAnomalies(), fetchForecast()])
      .then(([b, a, f]) => { setBudgets(b.items); setAnomalies(a.items); setForecast(f); })
      .catch((e) => setError(String(e?.message ?? e)));
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    if (!canManage) return;
    api.get<{ items: { id: string; name: string }[] }>("/api/v1/customers?page_size=200")
      .then((r) => setCustomers(r.items.map((c) => ({ id: c.id, name: c.name })))).catch(() => undefined);
  }, [canManage]);

  async function runPass() {
    setBusy(true); setError(null);
    try {
      const res = await runAnomalyPass();
      setNotice(`Anomaly pass: ${res.created} new, ${res.updated} updated across ${res.subjects} subject(s)`);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "pass failed"); }
    finally { setBusy(false); }
  }

  async function runAlerts() {
    setBusy(true); setError(null);
    try {
      const res = await evaluateAlerts();
      setNotice(`Alert pass: ${res.evaluated} budgets evaluated — ${res.opened} alert(s) sent, `
        + `${res.closed} recovered, ${res.still_open} already open (no duplicates)`);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "alert pass failed"); }
    finally { setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Budgets & Anomalies"
        subtitle="Spend caps with straight-line burn projection, and month-over-month anomaly detection (robust median/MAD z-score — a statistical method, not ML)."
        actions={
          <>
            {canReview && <Button variant="secondary" loading={busy} onClick={runPass}>Run anomaly pass</Button>}
            {canAlert && <Button variant="secondary" loading={busy} onClick={runAlerts}>Run alert pass</Button>}
            {canManage && <Button onClick={() => setShowCreate(true)}>New budget</Button>}
          </>
        }
      />
      {notice && <p className="mb-3 rounded-lg bg-ink-50 px-3 py-2 text-xs text-ink-700 ring-1 ring-ink-200">{notice}</p>}
      {error ? <Card className="p-4"><ErrorState detail={error} onRetry={load} /></Card>
        : budgets === null ? <Spinner label="Loading budgets" /> : (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle>Budgets</CardTitle></CardHeader>
            <CardBody className="p-0">
              {budgets.length === 0 ? (
                <div className="p-5"><EmptyState title="No budgets yet" hint="Create a monthly cap for a customer or your whole book of business." /></div>
              ) : (
                <Table>
                  <THead><TR><TH>Budget</TH><TH>Period</TH><TH className="text-right">Actual</TH><TH className="text-right">Projected</TH><TH className="text-right">Cap</TH><TH>State</TH><TH /></TR></THead>
                  <TBody>
                    {budgets.map((b) => (
                      <TR key={b.id}>
                        <TD>
                          <span className="font-medium text-xs">{b.name}</span>
                          <div className="text-[10px] text-ink-400">{b.scope_kind}{b.provider ? ` · ${b.provider}` : ""}</div>
                        </TD>
                        <TD className="text-xs">{b.period_start.slice(0, 7)} <span className="text-ink-400">→</span> {b.period_end.slice(0, 7)}</TD>
                        <TD className="text-right text-xs"><MoneyCell value={b.actual} /></TD>
                        <TD className="text-right text-xs">
                          <MoneyCell value={b.projected_total} />
                          <div className="text-[10px] text-ink-400">{b.projected_pct}% of cap</div>
                        </TD>
                        <TD className="text-right text-xs"><MoneyCell value={b.amount} /></TD>
                        <TD>
                          {b.over_budget ? <Badge tone="negative">over budget</Badge>
                            : b.over_threshold ? <Badge tone="warning">over {b.alert_threshold_pct}%</Badge>
                            : <Badge tone="positive">on track</Badge>}
                          {b.alert_state?.breached && (
                            <div className="mt-0.5 text-[10px] text-amber-700">
                              alert sent · episode #{b.alert_state.alert_count}
                            </div>
                          )}
                        </TD>
                        <TD className="text-right">
                          {canManage && (
                            <Button size="sm" variant="ghost" onClick={async () => { await deleteBudget(b.id); load(); }}>Delete</Button>
                          )}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </CardBody>
          </Card>
          <div className="space-y-4">
            <Card>
              <CardHeader><CardTitle>Spend forecast</CardTitle></CardHeader>
              <CardBody>
                {!forecast || forecast.method === "insufficient_history" ? (
                  <p className="text-xs text-ink-400">Needs at least two months of ingested billing data.</p>
                ) : (
                  <>
                    <p className="mb-2 text-[10px] uppercase tracking-wide text-ink-400">
                      method: {forecast.method} · avg MoM {forecast.avg_monthly_growth_pct}%
                    </p>
                    <ul className="space-y-1 text-xs">
                      {[...forecast.months.slice(-3), ...forecast.forecast].map((m, i) => (
                        <li key={m.month} className="flex justify-between">
                          <span className={i >= forecast.months.slice(-3).length ? "italic text-ink-400" : ""}>
                            {m.month}{i >= forecast.months.slice(-3).length ? " (proj)" : ""}
                          </span>
                          <MoneyCell value={m.amount} />
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </CardBody>
            </Card>
            <Card>
              <CardHeader><CardTitle>Open anomalies</CardTitle></CardHeader>
              <CardBody className="p-0">
                {anomalies === null ? <Spinner label="Loading" /> : anomalies.length === 0 ? (
                  <div className="p-4 text-xs text-ink-400">None detected. Run the pass after new ingestion.</div>
                ) : (
                  <ul className="divide-y divide-ink-100">
                    {anomalies.slice(0, 8).map((a) => (
                      <li key={a.id} className="flex items-center gap-2 px-4 py-2.5 text-xs">
                        <Badge tone={a.kind === "cost_spike" ? "warning" : "info"}>{a.kind === "cost_spike" ? "spike" : "drop"}</Badge>
                        <span className="flex-1 truncate">{a.service ?? "org spend"} · {a.detected_on.slice(0, 7)}</span>
                        <span className="text-ink-400">z={a.z_score}</span>
                        {canReview && a.status === "open" && (
                          <Button size="sm" variant="ghost" onClick={async () => {
                            await reviewAnomaly(a.id, "acknowledged", "reviewed in console"); load();
                          }}>Ack</Button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>
          </div>
        </div>
      )}
      {showCreate && (
        <CreateBudgetModal customers={customers} onClose={() => setShowCreate(false)}
          onDone={() => { setShowCreate(false); load(); }} />
      )}
    </div>
  );
}

function CreateBudgetModal({ customers, onClose, onDone }: {
  customers: { id: string; name: string }[]; onClose: () => void; onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("10000");
  const [customerId, setCustomerId] = useState("");
  const [month, setMonth] = useState("2026-09");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true); setError(null);
    try {
      const [y, m] = month.split("-").map(Number);
      const start = `${y}-${String(m).padStart(2, "0")}-01T00:00:00+00:00`;
      const end = m === 12 ? `${y + 1}-01-01T00:00:00+00:00` : `${y}-${String(m + 1).padStart(2, "0")}-01T00:00:00+00:00`;
      await createBudget({
        name, amount, period_start: start, period_end: end,
        customer_id: customerId || null, alert_threshold_pct: 80,
      });
      onDone();
    } catch (e) { setError(e instanceof Error ? e.message : "create failed"); }
    finally { setBusy(false); }
  }

  return (
    <Modal open title="New budget" onClose={onClose}>
      <div className="space-y-3">
        <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme monthly cap" />}</Field>
        <Field label="Customer (optional — empty caps the whole partner org)">
          {(id) => (
            <Select id={id} value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
              <option value="">All customers</option>
              {customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
          )}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Amount (USD)">{(id) => <Input id={id} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" />}</Field>
          <Field label="Month">{(id) => <Input id={id} value={month} onChange={(e) => setMonth(e.target.value)} placeholder="YYYY-MM" />}</Field>
        </div>
      </div>
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button loading={busy} disabled={!name || !amount} onClick={submit}>Create budget</Button>
      </div>
    </Modal>
  );
}
