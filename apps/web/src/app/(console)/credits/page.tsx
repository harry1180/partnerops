"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import { fetchCustomers } from "@/lib/hooks";
import {
  allocateCredit, createCredit, fetchCredits, type CreditRow,
} from "@/lib/ops-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, Field, Input,
  Modal, MoneyCell, PageHeader, Select, Spinner, StatusPill, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const KINDS = ["promotional", "service", "refund", "marketplace", "edp_true_up", "manual"];

export default function CreditsPage() {
  const { me } = useApp();
  const canWrite = me?.permissions.includes("customer.write") ?? false;
  const [filter, setFilter] = useState("unallocated");
  const [rows, setRows] = useState<CreditRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [allocFor, setAllocFor] = useState<CreditRow | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchCredits(filter || undefined).then(setRows).catch((e) => setError(String(e?.message ?? e)));
  }, [filter]);
  useEffect(load, [load]);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Credits & Discounts"
        subtitle="Provider-issued credits, refunds and promo funds tracked here; pass-through vs retention follows the contract's sharing policy."
        actions={canWrite ? <Button onClick={() => setOpen(true)}>New credit</Button> : undefined}
        filters={
          <Select aria-label="Allocation filter" className="w-56" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All credits</option>
            <option value="unallocated">Unallocated</option>
            <option value="partially_allocated">Partially allocated</option>
            <option value="allocated">Fully allocated</option>
          </Select>
        }
      />
      {error ? <Card className="p-4"><ErrorState detail={error} onRetry={load} /></Card> :
        !rows ? <Spinner label="Loading credits" /> : rows.length === 0 ? (
        <Card className="p-5"><EmptyState title="No credits in this filter" hint="Credits appear here when tracked manually or imported from provider billing." /></Card>
      ) : (
        <Card>
          <CardHeader><CardTitle>{rows.length} credit{rows.length === 1 ? "" : "s"}</CardTitle></CardHeader>
          <CardBody className="p-0">
            <Table>
              <THead><TR><TH>Credit</TH><TH>Kind</TH><TH>Customer</TH><TH className="text-right">Total</TH><TH className="text-right">Remaining</TH><TH>Status</TH><TH>Expires</TH><TH /></TR></THead>
              <TBody>
                {rows.map((c) => (
                  <TR key={c.id}>
                    <TD>
                      <span className="font-medium">{c.display_name}</span>
                      <span className="ml-2 font-mono text-[10px] text-ink-400">{c.provider}</span>
                    </TD>
                    <TD><Badge tone="neutral">{c.kind.replace(/_/g, " ")}</Badge></TD>
                    <TD className="text-xs">{c.customer ?? <span className="text-ink-400">unattributed</span>}</TD>
                    <TD className="text-right text-xs"><MoneyCell value={c.amount_total} currency={c.currency} /></TD>
                    <TD className="text-right text-sm font-medium"><MoneyCell value={c.remaining} currency={c.currency} /></TD>
                    <TD><StatusPill status={c.allocation_status === "partially_allocated" ? "under_review" : c.allocation_status} /></TD>
                    <TD className="text-xs tabular-nums">{c.expires_at ? c.expires_at.slice(0, 10) : "—"}</TD>
                    <TD className="text-right">
                      {canWrite && c.allocation_status !== "allocated" && (
                        <Button size="sm" variant="secondary" onClick={() => setAllocFor(c)}>Allocate</Button>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardBody>
        </Card>
      )}
      {open && <CreateCreditModal onClose={() => setOpen(false)} onDone={() => { setOpen(false); load(); }} />}
      {allocFor && <AllocateModal credit={allocFor} onClose={() => setAllocFor(null)} onDone={() => { setAllocFor(null); load(); }} />}
    </div>
  );
}

function CreateCreditModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("service");
  const [amount, setAmount] = useState("");
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await createCredit({
        display_name: name, kind, amount_total: amount,
        expires_at: expires ? `${expires}T00:00:00+00:00` : null,
      });
      onDone();
    } catch (err) { setError(err instanceof Error ? err.message : "failed"); }
    finally { setBusy(false); }
  }

  return (
    <Modal open title="Track a provider credit" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Name" required>
          { (id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} placeholder="e.g. Q3 migration good-will" /> }
        </Field>
        <Field label="Kind">
          {(id) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => <option key={k} value={k}>{k.replace(/_/g, " ")}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Amount (USD)" required>
          { (id) => <Input id={id} value={amount} onChange={(e) => setAmount(e.target.value)} required placeholder="1000.00" /> }
        </Field>
        <Field label="Expires (optional)">
          { (id) => <Input id={id} type="date" value={expires} onChange={(e) => setExpires(e.target.value)} /> }
        </Field>
        {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" loading={busy}>Create</Button></div>
      </form>
    </Modal>
  );
}

function AllocateModal({ credit, onClose, onDone }: { credit: CreditRow; onClose: () => void; onDone: () => void }) {
  const [customers, setCustomers] = useState<{ id: string; name: string }[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [amount, setAmount] = useState(credit.remaining);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchCustomers(1).then((p) => {
      setCustomers(p.items.map((i) => ({ id: i.id, name: i.name })));
      setCustomerId(p.items[0]?.id ?? "");
    }).catch(() => setCustomers([]));
  }, []);

  return (
    <Modal open title={`Allocate ${credit.display_name}`} onClose={onClose}>
      <p className="mb-3 text-xs text-ink-500">
        Remaining: <MoneyCell value={credit.remaining} currency={credit.currency} />. Allocation attributes
        the credit to a customer so their next pricing run can apply it per contract policy.
      </p>
      <Field label="Customer">
        {(id) => (
          <Select id={id} value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
            {customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        )}
      </Field>
      <div className="mt-3">
        <Field label="Amount">
          { (id) => <Input id={id} value={amount} onChange={(e) => setAmount(e.target.value)} /> }
        </Field>
      </div>
      {error && <p role="alert" className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button loading={busy} disabled={!customerId || !amount} onClick={async () => {
          setBusy(true); setError(null);
          try { await allocateCredit(credit.id, customerId, amount); onDone(); }
          catch (e) { setError(e instanceof Error ? e.message : "allocation failed"); }
          finally { setBusy(false); }
        }}>Allocate</Button>
      </div>
    </Modal>
  );
}
