"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import { decideApproval, fetchApprovals, type ApprovalRow } from "@/lib/ops-api";
import {
  Badge, Button, Card, CardBody, EmptyState, ErrorState, Field,
  Modal, MoneyCell, PageHeader, Select, Spinner, StatusPill,
  Textarea,
} from "@cloudpartnerops/ui";

const KIND_LABEL: Record<string, string> = {
  rule_publish: "rule publish",
  contract_overlap: "contract overlap",
  contract_activate: "contract activation",
  manual_adjustment: "manual adjustment",
  recon_waiver: "reconciliation waiver",
  period_close_waiver: "period close",
  invoice_issue: "invoice issue",
};

export default function ApprovalsPage() {
  const { me } = useApp();
  const canDecide = (me?.permissions.includes("contract.approve")
    || me?.permissions.includes("rule.approve")
    || me?.permissions.includes("recon.waive")) ?? false;
  const [statusFilter, setStatusFilter] = useState("pending");
  const [rows, setRows] = useState<ApprovalRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decideFor, setDecideFor] = useState<ApprovalRow | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchApprovals(1, statusFilter || undefined).then((p) => setRows(p.items))
      .catch((e) => setError(String(e?.message ?? e)));
  }, [statusFilter]);
  useEffect(load, [load]);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Approvals"
        subtitle="Maker-checker queue: the person who requests a high-impact change can never approve it."
        filters={
          <Select aria-label="Status filter" className="w-44" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="denied">Denied</option>
            <option value="">All</option>
          </Select>
        }
      />
      {error ? <Card className="p-4"><ErrorState detail={error} onRetry={load} /></Card> :
        !rows ? <Spinner label="Loading approvals" /> : rows.length === 0 ? (
        <Card className="p-5"><EmptyState title={statusFilter === "pending" ? "Nothing awaiting approval" : "No approvals in this filter"}
          hint="High-impact rule publishes, reconciliation waivers and contract overlaps land here for a second pair of eyes." /></Card>
      ) : (
        <div className="space-y-3">
          {rows.map((a) => (
            <Card key={a.id}>
              <CardBody>
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-ink-500">{a.request_number}</span>
                      <Badge tone="neutral">{KIND_LABEL[a.kind] ?? a.kind}</Badge>
                      <StatusPill status={a.status} />
                    </div>
                    <p className="mt-1 text-sm">{a.summary}</p>
                    <p className="mt-1 text-xs text-ink-500">
                      Requested by {a.maker} ({a.maker_email}) · {new Date(a.created_at).toLocaleString()}
                      {a.impact_amount && <> · impact ≈ <MoneyCell value={a.impact_amount} currency={a.currency} /></>}
                      {a.decided_at && <> · decided {new Date(a.decided_at).toLocaleString()} — {a.decision_note}</>}
                    </p>
                  </div>
                  {a.status === "pending" && canDecide && (
                    <Button size="sm" onClick={() => setDecideFor(a)}>Review</Button>
                  )}
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
      {decideFor && <DecideModal row={decideFor} onClose={() => setDecideFor(null)} onDone={() => { setDecideFor(null); load(); }} />}
    </div>
  );
}

function DecideModal({ row, onClose, onDone }: { row: ApprovalRow; onClose: () => void; onDone: () => void }) {
  const { me } = useApp();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approved" | "denied" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const self = row.maker_email === me?.email;

  async function decide(d: "approved" | "denied") {
    setBusy(d); setError(null);
    try { await decideApproval(row.id, d, note); onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "decision failed"); }
    finally { setBusy(null); }
  }

  return (
    <Modal open title={`Decide ${row.request_number}`} onClose={onClose}>
      <p className="text-sm">{row.summary}</p>
      <p className="mt-1 text-xs text-ink-500">{KIND_LABEL[row.kind] ?? row.kind} · requested by {row.maker}</p>
      {self && (
        <p role="alert" className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-warning ring-1 ring-amber-200">
          You requested this approval — maker-checker will reject your decision.
        </p>
      )}
      <div className="mt-4">
        <Field label="Decision note" required hint="Recorded in the audit trail.">
          {(id) => <Textarea id={id} value={note} onChange={(e) => setNote(e.target.value)} rows={3} minLength={3} />}
        </Field>
      </div>
      {error && <p role="alert" className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant="danger" loading={busy === "denied"} disabled={note.trim().length < 3 || self}
                onClick={() => void decide("denied")}>Deny</Button>
        <Button loading={busy === "approved"} disabled={note.trim().length < 3 || self}
                onClick={() => void decide("approved")}>Approve</Button>
      </div>
    </Modal>
  );
}
