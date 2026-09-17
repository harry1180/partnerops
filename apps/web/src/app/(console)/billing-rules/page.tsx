"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import {
  createRule, createRuleVersion, fetchRules, publishRuleVersion, type RuleRow,
} from "@/lib/billing-api";
import { api } from "@/lib/api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, ErrorState, Field, Input, Modal,
  PageHeader, Select, Spinner, StatusPill, Table, TBody, TD, TH, THead, TR, Textarea,
} from "@cloudpartnerops/ui";

const RULE_TYPES = [
  "percentage_markup", "percentage_discount", "fixed_recurring", "one_time",
  "fixed_unit_rate", "tiered", "minimum_monthly", "maximum_cap",
  "managed_service_fee", "support_charge", "credit_pass_through", "credit_retention",
  "custom_service_charge", "sku_override", "marketplace_adjustment",
  "tax_adjustment", "currency_conversion", "data_exclusion", "promotional_credit",
  "manual_adjustment",
];

export default function BillingRulesPage() {
  const { me } = useApp();
  const canWrite = me?.permissions.includes("rule.write") ?? false;
  const [rules, setRules] = useState<RuleRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [sandboxFor, setSandboxFor] = useState<{ rule: RuleRow; versionId: string } | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchRules().then(setRules).catch((e) => setError(String(e?.message ?? e)));
  }, []);
  useEffect(load, [load]);

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Billing Rules"
        subtitle="Deterministic, versioned, explainable. Each run snapshots the exact rule versions used."
        actions={canWrite ? <Button onClick={() => setOpen(true)}>New rule</Button> : undefined}
      />
      {error && <Card className="mb-4 p-4"><ErrorState detail={error} onRetry={load} /></Card>}
      {!rules ? <Spinner label="Loading rules" /> : (
        <div className="space-y-3">
          {rules.map((r) => (
            <Card key={r.id}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-ink-500">{r.code}</span>
                    <CardTitle>{r.name}</CardTitle>
                    <Badge tone="neutral">{r.rule_type.replace(/_/g, " ")}</Badge>
                  </div>
                  <StatusPill status={r.status ?? (r.versions.some((v) => v.status === "published") ? "active" : r.versions.some((v) => v.status === "draft") ? "draft" : "retired")} />
                </div>
              </CardHeader>
              <CardBody className="p-0">
                <Table>
                  <THead><TR><TH>v#</TH><TH>status</TH><TH>prio</TH><TH>order</TH><TH>parameters</TH><TH>impact est.</TH><TH /></TR></THead>
                  <TBody>
                    {r.versions.map((v) => (
                      <TR key={v.id}>
                        <TD className="font-mono">v{v.version_number}</TD>
                        <TD><StatusPill status={v.status} /></TD>
                        <TD className="tabular-nums">{v.priority}</TD>
                        <TD className="tabular-nums">{v.calc_order}</TD>
                        <TD><code className="text-[11px] text-ink-600">{JSON.stringify(v.parameters)}</code></TD>
                        <TD className="text-xs">{v.estimated_impact_monthly ?? "—"}</TD>
                        <TD className="text-right">
                          <div className="flex justify-end gap-1">
                            <Button size="sm" variant="ghost" onClick={() => setSandboxFor({ rule: r, versionId: v.id })}>
                              Test rule
                            </Button>
                            {v.status === "draft" && canWrite && (
                              <Button size="sm" onClick={async () => {
                                try { await publishRuleVersion(v.id); load(); }
                                catch (e) { alert(e instanceof Error ? e.message : "publish failed"); }
                              }}>
                                Publish
                              </Button>
                            )}
                          </div>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </CardBody>
            </Card>
          ))}
          {rules.length === 0 && <Card className="p-5 text-sm text-ink-500">No rules yet — create a markup rule to start billing.</Card>}
        </div>
      )}
      {open && <CreateRule onClose={() => setOpen(false)} onDone={() => { setOpen(false); load(); }} />}
      {sandboxFor && <SandboxModal rule={sandboxFor.rule} versionId={sandboxFor.versionId} onClose={() => setSandboxFor(null)} />}
    </div>
  );
}

function CreateRule({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState("percentage_markup");
  const [paramsJson, setParamsJson] = useState('{"percent": "12"}');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const { id } = await createRule({ code, name, rule_type: type });
      let params: Record<string, unknown>;
      try { params = JSON.parse(paramsJson); } catch { throw new Error("parameters must be valid JSON"); }
      await createRuleVersion(id, { parameters: { ...params, type }, priority: 10, calc_order: 10 });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally { setBusy(false); }
  }

  return (
    <Modal open title="New billing rule" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Code" required>
          { (id) => <Input id={id} value={code} onChange={(e) => setCode(e.target.value)} required className="font-mono uppercase" minLength={2} /> }
        </Field>
        <Field label="Name" required>
          { (id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /> }
        </Field>
        <Field label="Rule type" required>
          {(id) => (
            <Select id={id} value={type} onChange={(e) => {
              setType(e.target.value);
              setParamsJson(
                ["fixed_recurring", "one_time", "minimum_monthly", "maximum_cap", "manual_adjustment", "promotional_credit"].includes(e.target.value)
                  ? '{"amount": "350.00"}'
                  : e.target.value === "credit_pass_through" || e.target.value === "credit_retention"
                    ? '{"percent": "100"}'
                    : e.target.value === "fixed_unit_rate"
                      ? '{"rate": "0.10"}'
                      : '{"percent": "12"}',
              );
            }}>
              {RULE_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Parameters (JSON)" hint="Decimal amounts as strings, e.g. {&quot;percent&quot;: &quot;12&quot;}">
          {(id) => <Textarea id={id} value={paramsJson} onChange={(e) => setParamsJson(e.target.value)} className="font-mono text-xs" />}
        </Field>
        <p className="text-xs text-ink-400">Creates rule + version v2 draft (v1 is an empty template). Publish after testing.</p>
        {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" loading={busy}>Create</Button></div>
      </form>
    </Modal>
  );
}

interface SandboxResult {
  baseline_total: string; sandbox_total: string; delta: string; delta_pct: string | null;
  affected_groups: number; rule_hits: number;
  groups_preview: { group: string; before: string; after: string }[];
}

function SandboxModal({ rule, versionId, onClose }: { rule: RuleRow; versionId: string; onClose: () => void }) {
  const [period, setPeriod] = useState("2026-06");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true); setError(null); setResult(null);
    const [y, m] = period.split("-").map(Number);
    const start = new Date(Date.UTC(y, m - 1, 1)).toISOString();
    const end = new Date(Date.UTC(y, m, 1)).toISOString();
    try {
      setResult(await api.post<SandboxResult>(`/api/v1/billing-rule-versions/${versionId}/sandbox`,
        { period_start: start, period_end: end }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "sandbox failed");
    } finally { setBusy(false); }
  }

  return (
    <Modal open wide title={`Test rule sandbox — ${rule.code}`} onClose={onClose}>
      <div className="flex items-end gap-3">
        <Field label="Billing period">
          {(id) => <Input id={id} type="month" value={period} onChange={(e) => setPeriod(e.target.value)} />}
        </Field>
        <Button loading={busy} onClick={run}>Preview on history</Button>
        <Badge tone="info">read-only — no run persisted</Badge>
      </div>
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
      {result && (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-3 gap-3 text-sm">
            <div className="rounded-lg border border-ink-200 px-3 py-2">
              <div className="text-xs text-ink-500">Baseline (contract basis)</div>
              <div className="font-display text-lg tabular-nums">{result.baseline_total}</div>
            </div>
            <div className="rounded-lg border border-ink-200 px-3 py-2">
              <div className="text-xs text-ink-500">With this rule</div>
              <div className="font-display text-lg tabular-nums">{result.sandbox_total}</div>
            </div>
            <div className="rounded-lg border border-ink-200 px-3 py-2">
              <div className="text-xs text-ink-500">Delta {result.delta_pct ? `(${result.delta_pct}%)` : ""}</div>
              <div className={`font-display text-lg tabular-nums ${Number(result.delta) < 0 ? "text-negative" : "text-positive"}`}>{result.delta}</div>
            </div>
          </div>
          <Table>
            <THead><TR><TH>Group</TH><TH className="text-right">Before</TH><TH className="text-right">After</TH></TR></THead>
            <TBody>
              {result.groups_preview.map((g) => (
                <TR key={g.group}><TD className="text-xs">{g.group}</TD><TD className="text-right text-xs tabular-nums">{g.before}</TD><TD className="text-right text-xs tabular-nums">{g.after}</TD></TR>
              ))}
            </TBody>
          </Table>
          <p className="text-xs text-ink-400">Affected {result.rule_hits} groups. Evidence stored as a sandbox test for approval.</p>
        </div>
      )}
    </Modal>
  );
}
