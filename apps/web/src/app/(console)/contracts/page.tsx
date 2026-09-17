"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useApp } from "@/lib/app-state";
import { api } from "@/lib/api";
import { fetchCustomers } from "@/lib/hooks";
import {
  activateContract, createContract, fetchContracts, setBindings, type ContractRow,
} from "@/lib/billing-api";
import {
  Badge, Button, Card, ErrorState, Field, Input, Modal, PageHeader, Select, Spinner,
  StatusPill,
} from "@cloudpartnerops/ui";

const PRICING_BASES = [
  "provider_billed", "list", "ondemand_equivalent", "net_after_credits",
];

export default function ContractsPage() {
  const { me } = useApp();
  const canWrite = me?.permissions.includes("contract.write") ?? false;
  const [rows, setRows] = useState<ContractRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [bindingFor, setBindingFor] = useState<{ contract: ContractRow; versionId: string } | null>(null);

  const load = () => {
    setError(null);
    fetchContracts().then(setRows).catch((e) => setError(String(e?.message ?? e)));
  };
  useEffect(load, []);

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Contracts"
        subtitle="Versioned customer agreements. Editing an active contract creates a new version — used versions are never rewritten."
        actions={canWrite ? <Button onClick={() => setOpen(true)}>New contract</Button> : undefined}
      />
      {error && <Card className="mb-4 p-4"><ErrorState detail={error} onRetry={load} /></Card>}
      {!rows ? <Spinner label="Loading contracts" /> : rows.length === 0 ? (
        <Card className="p-5 text-sm text-ink-500">No contracts yet. Create one for a customer, then bind billing rules.</Card>
      ) : (
        <div className="space-y-3">
          {rows.map((c) => (
            <Card key={c.id}>
              <div className="flex items-start justify-between px-5 py-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-ink-500">{c.code}</span>
                    <Link href={`/customers/${c.customer_id}`} className="cpo-focus rounded font-display text-base font-semibold hover:underline">{c.name}</Link>
                  </div>
                  <div className="mt-1 text-xs text-ink-500">
                    {c.versions.length} version{c.versions.length === 1 ? "" : "s"} ·{" "}
                    {c.versions[0] ? `${c.versions[0].currency} · ${c.versions[0].pricing_basis.replace(/_/g, " ")}` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <StatusPill status={c.status} />
                </div>
              </div>
              <div className="border-t border-ink-100 px-5 py-3">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-ink-500">
                      <th className="pb-1 font-medium">v#</th><th className="pb-1 font-medium">status</th>
                      <th className="pb-1 font-medium">effective</th><th className="pb-1 font-medium">rules bound</th><th />
                    </tr>
                  </thead>
                  <tbody>
                    {c.versions.map((v) => (
                      <tr key={v.id} className="border-t border-ink-100">
                        <td className="py-1.5 font-mono">v{v.version_number}</td>
                        <td><StatusPill status={v.status} /></td>
                        <td className="py-1.5 tabular-nums">{v.effective_start.slice(0, 10)} → {v.effective_end ? v.effective_end.slice(0, 10) : "open"}</td>
                        <td className="py-1.5">{v.rule_bindings.length}</td>
                        <td className="py-1.5 text-right">
                          {v.status === "draft" && canWrite && (
                            <div className="flex justify-end gap-2">
                              <Button size="sm" variant="secondary" onClick={() => setBindingFor({ contract: c, versionId: v.id })}>
                                Bind rules
                              </Button>
                              <Button size="sm" onClick={async () => {
                                try {
                                  await activateContract(v.id);
                                  load();
                                } catch (e) {
                                  alert(e instanceof Error ? e.message : "activation failed");
                                }
                              }}>
                                Activate
                              </Button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}
        </div>
      )}
      {open && <CreateContract onClose={() => setOpen(false)} onDone={() => { setOpen(false); load(); }} />}
      {bindingFor && (
        <BindingModal contract={bindingFor.contract} versionId={bindingFor.versionId}
                      onClose={() => setBindingFor(null)}
                      onDone={() => { setBindingFor(null); load(); }} />
      )}
    </div>
  );
}

function CreateContract({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const cust = useApp();
  const [customers, setCustomers] = useState<{ id: string; name: string; code: string }[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [start, setStart] = useState("2026-06-01");
  const [minimum, setMinimum] = useState("");
  const [basis, setBasis] = useState("provider_billed");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchCustomers(1).then((p) => {
      setCustomers(p.items.map((i) => ({ id: i.id, name: i.name, code: i.code })));
      // functional update: a late-resolving (re)fetch must never overwrite a
      // selection the user already made
      setCustomerId((cur) => cur || (p.items[0]?.id ?? ""));
    }).catch(() => setCustomers([]));
  }, [cust.me?.id]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await createContract({
        customer_id: customerId, code, name,
        effective_start: `${start}T00:00:00+00:00`,
        pricing_basis: basis,
        minimum_monthly: minimum || null,
      });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally { setBusy(false); }
  }

  return (
    <Modal open title="New contract" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Customer" required>
          {(id) => (
            <Select id={id} value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
              {customers.length === 0 && <option value="">— no customers —</option>}
              {customers.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.code})</option>)}
            </Select>
          )}
        </Field>
        <Field label="Code" required>
          { (id) => <Input id={id} value={code} onChange={(e) => setCode(e.target.value)} required className="font-mono uppercase" minLength={2} /> }
        </Field>
        <Field label="Name" required>
          { (id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /> }
        </Field>
        <Field label="Effective from" required>
          { (id) => <Input id={id} type="date" value={start} onChange={(e) => setStart(e.target.value)} required /> }
        </Field>
        <Field label="Pricing basis">
          {(id) => (
            <Select id={id} value={basis} onChange={(e) => setBasis(e.target.value)}>
              {PRICING_BASES.map((b) => <option key={b} value={b}>{b.replace(/_/g, " ")}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Minimum monthly (optional)" hint="Contract floor; a separate billing rule can enforce it.">
          { (id) => <Input id={id} value={minimum} onChange={(e) => setMinimum(e.target.value)} placeholder="e.g. 1500.00" /> }
        </Field>
        {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" loading={busy}>Create v1 (draft)</Button></div>
      </form>
    </Modal>
  );
}

interface RuleOpt { id: string; code: string; name: string; rule_type: string; publishedVersionId: string | null }

function BindingModal({ contract, versionId, onClose, onDone }: {
  contract: ContractRow; versionId: string; onClose: () => void; onDone: () => void;
}) {
  const [rules, setRules] = useState<RuleOpt[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ id: string; code: string; name: string; rule_type: string; versions: { id: string; status: string }[] }[]>("/api/v1/billing-rules")
      .then((rs) => setRules(rs.map((r) => ({
        id: r.id, code: r.code, name: r.name, rule_type: r.rule_type,
        publishedVersionId: r.versions.find((v) => v.status === "published")?.id ?? null,
      }))))
      .catch((e) => setError(String(e?.message ?? e)));
  }, []);

  async function submit() {
    setBusy(true); setError(null);
    const chosen = rules.filter((r) => selected[r.id] && r.publishedVersionId);
    try {
      await setBindings(versionId, chosen.map((r, i) => ({ rule_id: r.id, rule_version_id: r.publishedVersionId, order: i + 1 })));
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "binding failed");
    } finally { setBusy(false); }
  }

  return (
    <Modal open title={`Bind billing rules — ${contract.name}`} onClose={onClose}>
      <p className="mb-3 text-xs text-ink-500">
        Only published rule versions can be pinned. Pricing runs freeze the bound versions; later edits create new versions without touching this contract.
      </p>
      {rules.length === 0 && !error && <Spinner label="Loading rules" />}
      <ul className="max-h-72 space-y-2 overflow-y-auto">
        {rules.map((r) => (
          <li key={r.id}>
            <label className={`cpo-focus flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${r.publishedVersionId ? "cursor-pointer" : "opacity-50"}`}>
              <input type="checkbox" className="mt-1" disabled={!r.publishedVersionId}
                     checked={!!selected[r.id]}
                     onChange={(e) => setSelected({ ...selected, [r.id]: e.target.checked })} />
              <span>
                <span className="font-mono text-xs">{r.code}</span> <Badge tone="neutral">{r.rule_type.replace(/_/g, " ")}</Badge>
                {!r.publishedVersionId && <span className="ml-2 text-xs text-ink-400">no published version</span>}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button loading={busy} onClick={submit}>Save bindings</Button>
      </div>
    </Modal>
  );
}
