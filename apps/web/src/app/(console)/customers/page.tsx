"use client";

import { useState } from "react";
import Link from "next/link";
import { useApp } from "@/lib/app-state";
import { api } from "@/lib/api";
import { fetchCustomers, fetchOrgs, useAsync, type CustomerRow } from "@/lib/hooks";
import {
  Badge, Button, Card, ComingLater, ErrorState, Field, Input, Modal, PageHeader,
  Pagination, Select, Spinner, StatusPill, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

export default function CustomersPage() {
  const { me, refresh } = useApp();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const canWrite = me?.permissions.includes("customer.write") ?? false;

  const data = useAsync(() => fetchCustomers(page, search || undefined), [page, search]);
  const orgs = useAsync(fetchOrgs, []);

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Customers"
        subtitle="End customers you bill, grouped by partner organization. Every row is RLS-scoped to your subtree."
        actions={
          canWrite ? (
            <Button onClick={() => setCreateOpen(true)}>Add customer</Button>
          ) : (
            <Badge tone="neutral">read-only</Badge>
          )
        }
        filters={
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setPage(1);
              data.reload();
            }}
          >
            <Input
              aria-label="Search customers"
              placeholder="Search name or code…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-64"
            />
            <Button type="submit" variant="secondary" size="sm">
              Search
            </Button>
          </form>
        }
      />

      <Card>
        {data.loading ? (
          <div className="p-5">
            <Spinner label="Loading customers" />
          </div>
        ) : data.error ? (
          <div className="p-5">
            <ErrorState detail={data.error} onRetry={data.reload} />
          </div>
        ) : (data.data?.items.length ?? 0) === 0 ? (
          <div className="p-5">
            <div className="rounded-xl border border-dashed border-ink-200 px-6 py-10 text-center">
              <p className="text-sm font-medium text-ink-700">No customers yet in your scope</p>
              <p className="mt-1 text-xs text-ink-400">
                {canWrite ? "Use Add customer to create your first one." : "Ask a partner admin to provision customers."}
              </p>
            </div>
          </div>
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH>Code</TH>
                  <TH>Customer</TH>
                  <TH>Account families</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Target margin</TH>
                </TR>
              </THead>
              <TBody>
                {data.data!.items.map((c: CustomerRow) => (
                  <TR key={c.id}>
                    <TD className="font-mono text-xs">{c.code}</TD>
                    <TD>
                      <Link href={`/customers/${c.id}`} className="cpo-focus rounded font-medium text-ink-900 underline-offset-2 hover:underline">
                        {c.name}
                      </Link>
                    </TD>
                    <TD className="tabular-nums">{c.account_family_count ?? 0}</TD>
                    <TD>
                      <StatusPill status={c.status} />
                    </TD>
                    <TD className="text-right tabular-nums">
                      {c.target_margin_pct !== null ? `${c.target_margin_pct.toFixed(1)}%` : <span className="text-ink-400">—</span>}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <div className="px-3">
              <Pagination
                page={data.data!.page}
                pageSize={data.data!.page_size}
                total={data.data!.total}
                onPage={setPage}
              />
            </div>
          </>
        )}
      </Card>

      {createOpen && (
        <CreateCustomerModal
          orgs={orgs.data ?? []}
          onClose={() => setCreateOpen(false)}
          onCreated={async () => {
            setCreateOpen(false);
            data.reload();
            await refresh();
          }}
        />
      )}

      <div className="mt-6">
        <ComingLater feature="Cloud account mapping, usage and contract tabs per customer" phase={1} />
      </div>
    </div>
  );
}

function CreateCustomerModal({
  orgs,
  onClose,
  onCreated,
}: {
  orgs: { id: string; name: string; kind: string }[];
  onClose: () => void;
  onCreated: () => void | Promise<void>;
}) {
  const partners = orgs.filter((o) => o.kind === "reseller" || o.kind === "distributor");
  const [orgId, setOrgId] = useState(partners[0]?.id ?? "");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!orgId) {
      setError("Pick a partner organization first (create one in Administration).");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/v1/orgs/${orgId}/customers`, {
        name,
        code: code.toUpperCase(),
        billing_email: email || null,
      });
      await onCreated();
    } catch (err) {
      setError(err instanceof Error ? `Could not create: ${err.message}` : "Could not create customer.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open title="Add customer" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Partner organization" required hint="Who bills this customer.">
          {(id) => (
            <Select id={id} value={orgId} onChange={(e) => setOrgId(e.target.value)}>
              {partners.length === 0 && <option value="">— none in your scope —</option>}
              {partners.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name} ({o.kind})
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Customer name" required>
          {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />}
        </Field>
        <Field label="Code" required hint="Short unique code shown on invoices.">
          {(id) => (
            <Input id={id} value={code} onChange={(e) => setCode(e.target.value)} required minLength={2} maxLength={16} className="font-mono uppercase" />
          )}
        </Field>
        <Field label="Billing email">
          {(id) => <Input id={id} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />}
        </Field>
        {error && (
          <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy}>
            Create customer
          </Button>
        </div>
      </form>
    </Modal>
  );
}
