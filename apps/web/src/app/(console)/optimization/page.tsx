"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import {
  decideRecommendation, fetchRecommendations, fetchTagCompliance, fetchUnitEconomics,
  runRecommendationPass, type RecRow, type TagCompliance, type UnitEconomics,
} from "@/lib/finops-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, Modal,
  MoneyCell, PageHeader, Select, Spinner, StatusPill, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const KIND_LABEL: Record<string, string> = {
  idle_resource: "Idle resource",
  rightsizing: "Right-size",
  commitment_gap: "Commitment gap",
  marketplace_review: "Marketplace review",
};

export default function OptimizationPage() {
  const { me } = useApp();
  const canReview = me?.permissions.includes("optimization.review") ?? false;
  const [statusFilter, setStatusFilter] = useState("open");
  const [recs, setRecs] = useState<{ items: RecRow[]; open_estimated_total: string; realized_total: string } | null>(null);
  const [unit, setUnit] = useState<UnitEconomics | null>(null);
  const [tags, setTags] = useState<TagCompliance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState<RecRow | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([fetchRecommendations(statusFilter), fetchUnitEconomics(), fetchTagCompliance()])
      .then(([r, u, t]) => { setRecs(r); setUnit(u); setTags(t); })
      .catch((e) => setError(String(e?.message ?? e)));
  }, [statusFilter]);
  useEffect(load, [load]);

  async function runPass() {
    setBusy(true);
    try { await runRecommendationPass(); load(); }
    catch (e) { setError(e instanceof Error ? e.message : "pass failed"); }
    finally { setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Optimization"
        subtitle="Suggestions computed from your ingested billing data with evidence attached. The platform advises; it never acts on provider resources."
        actions={canReview ? <Button variant="secondary" loading={busy} onClick={runPass}>Recompute recommendations</Button> : undefined}
        filters={
          <Select aria-label="Status" className="w-40" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="open">Open</option>
            <option value="accepted">Accepted</option>
            <option value="dismissed">Dismissed</option>
            <option value="">All</option>
          </Select>
        }
      />
      {error ? <Card className="p-4"><ErrorState detail={error} onRetry={load} /></Card>
        : recs === null ? <Spinner label="Loading recommendations" /> : (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>{recs.items.length} recommendation{recs.items.length === 1 ? "" : "s"}</CardTitle>
                <div className="flex gap-4 text-xs text-ink-500">
                  <span>open estimated/mo <MoneyCell value={recs.open_estimated_total} /></span>
                  <span>realized <MoneyCell value={recs.realized_total} /></span>
                </div>
              </div>
            </CardHeader>
            <CardBody className="p-0">
              {recs.items.length === 0 ? (
                <div className="p-5"><EmptyState title="Nothing matches" hint="Recommendations appear after billing data is ingested and the pass runs." /></div>
              ) : (
                <Table>
                  <THead><TR><TH>Recommendation</TH><TH>Kind</TH><TH className="text-right">Est. saving / mo</TH><TH>Confidence</TH><TH>State</TH><TH /></TR></THead>
                  <TBody>
                    {recs.items.map((r) => (
                      <TR key={r.id}>
                        <TD>
                          <button className="text-left text-xs font-medium hover:underline" onClick={() => setTarget(r)}>{r.title}</button>
                          <div className="text-[10px] text-ink-400">{r.service} · {r.resource_id?.slice(0, 40)}</div>
                        </TD>
                        <TD><Badge tone="neutral">{KIND_LABEL[r.kind] ?? r.kind}</Badge></TD>
                        <TD className="text-right text-xs">
                          {r.estimated_monthly_saving && Number(r.estimated_monthly_saving) > 0
                            ? <MoneyCell value={r.estimated_monthly_saving} /> : <span className="text-ink-400">—</span>}
                          {r.realized_savings && <div className="text-[10px] text-positive">realized {r.realized_savings}</div>}
                        </TD>
                        <TD className="text-xs capitalize">{r.confidence}</TD>
                        <TD><StatusPill status={r.status} /></TD>
                        <TD className="text-right whitespace-nowrap">
                          {canReview && r.status === "open" && (
                            <>
                              <Button size="sm" variant="ghost" onClick={() => decide(r, "accept")}>Accept</Button>
                              <Button size="sm" variant="ghost" onClick={() => decide(r, "dismiss")}>Dismiss</Button>
                            </>
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
              <CardHeader><CardTitle>Unit economics</CardTitle></CardHeader>
              <CardBody className="max-h-96 overflow-y-auto">
                {unit?.dimensions.filter((d) => d.items.length).map((d) => (
                  <div key={d.dimension} className="mb-3">
                    <div className="mb-1 text-[10px] uppercase tracking-widest text-ink-400">{d.dimension}</div>
                    <ul className="space-y-1 text-xs">
                      {d.items.slice(0, 6).map((i) => (
                        <li key={i.key} className="flex justify-between gap-2">
                          <span className="truncate">{i.key}</span><MoneyCell value={i.amount} />
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </CardBody>
            </Card>
            <Card>
              <CardHeader><CardTitle>Tag coverage</CardTitle></CardHeader>
              <CardBody>
                {tags && (
                  <ul className="space-y-2 text-xs">
                    {tags.coverage.map((c) => (
                      <li key={c.key} className="flex items-center gap-2">
                        <span className="w-28 truncate">{c.key}</span>
                        <div className="h-1.5 flex-1 rounded bg-ink-100">
                          <div className="h-full rounded bg-brand-primary" style={{ width: `${c.pct}%` }} />
                        </div>
                        <span className="w-14 text-right text-ink-500">{c.pct}%</span>
                      </li>
                    ))}
                    <li className="mt-2 flex justify-between border-t border-ink-100 pt-2">
                      <span>Unallocated cost</span>
                      <MoneyCell value={tags.unallocated_cost} />
                    </li>
                  </ul>
                )}
              </CardBody>
            </Card>
          </div>
        </div>
      )}
      {target && <RecModal rec={target} onClose={() => setTarget(null)} onDone={() => { setTarget(null); load(); }} canReview={canReview} />}
    </div>
  );

  async function decide(r: RecRow, decision: "accept" | "dismiss") {
    if (!canReview) return;
    try { await decideRecommendation(r.id, decision); load(); }
    catch (e) { setError(e instanceof Error ? e.message : "decision failed"); }
  }
}

function RecModal({ rec, onClose, onDone, canReview }: {
  rec: RecRow; onClose: () => void; onDone: () => void; canReview: boolean;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  async function decide(d: "accept" | "dismiss") {
    setBusy(true);
    try { await decideRecommendation(rec.id, d, note || undefined); onDone(); }
    finally { setBusy(false); }
  }
  return (
    <Modal open title={rec.title} onClose={onClose}>
      <p className="text-xs leading-5 text-ink-700">{rec.detail}</p>
      {rec.remediation && (
        <div className="mt-3 rounded-lg bg-ink-50 p-3 text-xs">
          <div className="mb-1 font-semibold text-ink-700">Suggested action</div>{rec.remediation}
        </div>
      )}
      <div className="mt-3 rounded-lg border border-ink-200 p-3 text-[11px]">
        <div className="mb-1 font-semibold uppercase tracking-wide text-ink-400">Evidence</div>
        <pre className="whitespace-pre-wrap font-mono text-ink-600">{JSON.stringify(rec.basis, null, 1)}</pre>
      </div>
      {rec.realized_basis && (
        <div className="mt-3 rounded-lg bg-emerald-50 p-3 text-[11px] ring-1 ring-emerald-200">
          <div className="mb-1 font-semibold uppercase tracking-wide">Measured savings basis</div>
          {JSON.stringify(rec.realized_basis)}
        </div>
      )}
      {canReview && rec.status === "open" && (
        <>
          <textarea aria-label="Decision note" value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="Optional note for the audit trail"
            className="mt-3 w-full rounded-lg border border-ink-200 px-3 py-2 text-xs" rows={2} />
          <div className="mt-3 flex justify-end gap-2">
            <Button variant="secondary" loading={busy} onClick={() => decide("dismiss")}>Dismiss</Button>
            <Button loading={busy} onClick={() => decide("accept")}>Accept</Button>
          </div>
        </>
      )}
    </Modal>
  );
}
