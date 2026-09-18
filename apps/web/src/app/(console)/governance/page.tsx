"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import {
  acknowledgeFinding, createPolicy, evaluatePolicies, fetchFindings, fetchPolicies,
  grantException, type FindingRow, type PolicyRow,
} from "@/lib/finops-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, Field,
  Input, Modal, PageHeader, Select, Spinner, StatusPill, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const KINDS: { value: string; label: string }[] = [
  { value: "required_tags", label: "Required allocation tags" },
  { value: "approved_regions", label: "Approved regions only" },
  { value: "unallocated_cost", label: "No unallocated spend" },
  { value: "idle_resources", label: "Idle leftover resources" },
  { value: "oversized_resources", label: "Oversized sustained spend" },
];

const SEV_TONE = { info: "neutral", low: "neutral", medium: "warning", high: "warning", critical: "negative" } as const;

export default function GovernancePage() {
  const { me } = useApp();
  const canManage = me?.permissions.includes("policy.manage") ?? false;
  const canReview = me?.permissions.includes("finding.review") ?? false;
  const [policies, setPolicies] = useState<PolicyRow[] | null>(null);
  const [findings, setFindings] = useState<FindingRow[] | null>(null);
  const [statusFilter, setStatusFilter] = useState("open");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [excTarget, setExcTarget] = useState<FindingRow | null>(null);
  const [detail, setDetail] = useState<FindingRow | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([fetchPolicies(), fetchFindings(statusFilter)])
      .then(([p, f]) => { setPolicies(p.items); setFindings(f.items); })
      .catch((e) => setError(String(e?.message ?? e)));
  }, [statusFilter]);
  useEffect(load, [load]);

  async function evaluate() {
    setBusy(true); setNotice(null);
    try {
      const r = await evaluatePolicies();
      setNotice(`Evaluated: ${r.new} new findings, ${r.open} open, ${r.remediated} auto-remediated, `
        + `${r.excepted} under exception, ${r.exceptions_expired} exceptions expired`);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "evaluation failed"); }
    finally { setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Governance"
        subtitle="Policies evaluated against ingested billing facts — every finding carries its evidence. Provider-config checks (encryption posture, public exposure) arrive with config connectors and are never faked."
        actions={
          <>
            {canManage && <Button variant="secondary" loading={busy} onClick={evaluate}>Evaluate now</Button>}
            {canManage && <Button onClick={() => setShowCreate(true)}>New policy</Button>}
          </>
        }
        filters={
          <Select aria-label="Finding status" className="w-40" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="open">Open findings</option>
            <option value="acknowledged">Acknowledged</option>
            <option value="excepted">Excepted</option>
            <option value="remediated">Remediated</option>
            <option value="">All</option>
          </Select>
        }
      />
      {notice && <p className="mb-3 rounded-lg bg-ink-50 px-3 py-2 text-xs text-ink-700 ring-1 ring-ink-200">{notice}</p>}
      {error ? <Card className="p-4"><ErrorState detail={error} onRetry={load} /></Card>
        : policies === null || findings === null ? <Spinner label="Loading governance" /> : (
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Policies</CardTitle></CardHeader>
            <CardBody className="p-0">
              {policies.length === 0 ? (
                <div className="p-5"><EmptyState title="No policies" hint="Create a policy to start continuous compliance evaluation." /></div>
              ) : (
                <Table>
                  <THead><TR><TH>Policy</TH><TH>Kind</TH><TH>Severity</TH><TH>Owner</TH><TH className="text-right">Open findings</TH><TH>Last evaluated</TH></TR></THead>
                  <TBody>
                    {policies.map((p) => (
                      <TR key={p.id}>
                        <TD>
                          <span className="text-xs font-medium">{p.name}</span>
                          {!p.enabled && <Badge tone="neutral">disabled</Badge>}
                        </TD>
                        <TD className="text-xs text-ink-500">{p.kind}</TD>
                        <TD><Badge tone={SEV_TONE[p.severity as keyof typeof SEV_TONE]}>{p.severity}</Badge></TD>
                        <TD className="text-xs">{p.owner_label ?? "—"}</TD>
                        <TD className="text-right text-xs">{p.open_findings}</TD>
                        <TD className="text-xs text-ink-400">{p.last_evaluated_at ? new Date(p.last_evaluated_at).toLocaleString() : "never"}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader><CardTitle>Findings</CardTitle></CardHeader>
            <CardBody className="p-0">
              {findings.length === 0 ? (
                <div className="p-5"><EmptyState title="No findings match" hint="Run evaluation after ingesting billing data." /></div>
              ) : (
                <Table>
                  <THead><TR><TH>Finding</TH><TH>Policy</TH><TH>Severity</TH><TH>State</TH><TH>Last seen</TH><TH /></TR></THead>
                  <TBody>
                    {findings.map((f) => (
                      <TR key={f.id}>
                        <TD>
                          <button className="text-left text-xs font-medium hover:underline" onClick={() => setDetail(f)}>{f.subject}</button>
                          {f.remediation && <div className="max-w-md truncate text-[10px] text-ink-400">{f.remediation}</div>}
                        </TD>
                        <TD className="text-xs">{f.policy_name}</TD>
                        <TD><Badge tone={SEV_TONE[f.severity as keyof typeof SEV_TONE]}>{f.severity}</Badge></TD>
                        <TD><StatusPill status={f.status} /></TD>
                        <TD className="text-xs text-ink-400">{new Date(f.last_seen).toLocaleDateString()}</TD>
                        <TD className="text-right whitespace-nowrap">
                          {canReview && f.status === "open" && (
                            <Button size="sm" variant="ghost" onClick={async () => { await acknowledgeFinding(f.id, "reviewed in console"); load(); }}>Acknowledge</Button>
                          )}
                          {canManage && !f.has_active_exception && f.status !== "remediated" && (
                            <Button size="sm" variant="ghost" onClick={() => setExcTarget(f)}>Except…</Button>
                          )}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </CardBody>
          </Card>
        </div>
      )}
      {showCreate && <CreatePolicyModal onClose={() => setShowCreate(false)} onDone={() => { setShowCreate(false); load(); }} />}
      {excTarget && <ExceptionModal finding={excTarget} onClose={() => setExcTarget(null)} onDone={() => { setExcTarget(null); load(); }} />}
      {detail && (
        <Modal open title={detail.subject} onClose={() => setDetail(null)}>
          <div className="rounded-lg border border-ink-200 p-3 text-[11px]">
            <div className="mb-1 font-semibold uppercase tracking-wide text-ink-400">Evidence</div>
            <pre className="whitespace-pre-wrap font-mono text-ink-700">{JSON.stringify(detail.evidence, null, 1)}</pre>
          </div>
          {detail.remediation && (
            <div className="mt-3 rounded-lg bg-ink-50 p-3 text-xs"><b>Remediation:</b> {detail.remediation}</div>
          )}
        </Modal>
      )}
    </div>
  );
}

function CreatePolicyModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState(KINDS[0].value);
  const [severity, setSeverity] = useState("medium");
  const [params, setParams] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true); setError(null);
    try {
      await createPolicy({
        name, kind, severity,
        parameters: params.trim() ? JSON.parse(params) : {},
      });
      onDone();
    } catch (e) { setError(e instanceof Error ? e.message : "create failed"); }
    finally { setBusy(false); }
  }

  return (
    <Modal open title="New policy" onClose={onClose}>
      <div className="space-y-3">
        <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="Approved regions" />}</Field>
        <Field label="Kind" hint="Only billing-observable checks evaluate today.">
          {(id) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Severity">
          {(id) => (
            <Select id={id} value={severity} onChange={(e) => setSeverity(e.target.value)}>
              {["low", "medium", "high", "critical"].map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Parameters (JSON)" hint='e.g. {"keys":["owner","cost_center"]} or {"regions":["us-east-1","eastus2"]}'>
          {(id) => <Input id={id} value={params} onChange={(e) => setParams(e.target.value)} className="font-mono text-xs" placeholder="{}" />}
        </Field>
      </div>
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button loading={busy} disabled={!name} onClick={submit}>Create policy</Button>
      </div>
    </Modal>
  );
}

function ExceptionModal({ finding, onClose, onDone }: {
  finding: FindingRow; onClose: () => void; onDone: () => void;
}) {
  const [reason, setReason] = useState("");
  const [expires, setExpires] = useState("2026-12-31");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    setBusy(true); setError(null);
    try {
      await grantException(finding.id, reason, `${expires}T23:59:59+00:00`);
      onDone();
    } catch (e) { setError(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); }
  }
  return (
    <Modal open title={`Exception for ${finding.subject}`} onClose={onClose}>
      <p className="mb-3 text-xs text-ink-500">
        The finding stays visible but is marked excepted until the expiry; an expired exception reopens it on the next evaluation. This is audited.
      </p>
      <Field label="Reason">{(id) => <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Business justification" />}</Field>
      <Field label="Expires">{(id) => <Input id={id} type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />}</Field>
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button loading={busy} disabled={reason.length < 3} onClick={submit}>Grant exception</Button>
      </div>
    </Modal>
  );
}
