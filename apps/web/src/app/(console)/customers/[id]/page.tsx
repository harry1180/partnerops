"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useApp } from "@/lib/app-state";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, ComingLater, ErrorState, Field, Input,
  Modal, PageHeader, Spinner, Textarea,
} from "@cloudpartnerops/ui";

interface CustomerDetail {
  id: string;
  code: string;
  name: string;
  status: string;
  org_path: string;
  owning_org_id: string;
  account_family_count: number | null;
}

interface Family {
  id: string;
  customer_id: string;
  name: string;
  description: string | null;
  account_count: number | null;
}

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useApp();
  const canWrite = me?.permissions.includes("customer.write") ?? false;
  const customer = useAsync<CustomerDetail>(() => api.get(`/api/v1/customers/${id}`), [id]);
  const families = useAsync<Family[]>(() => api.get(`/api/v1/customers/${id}/account-families`), [id]);
  const [open, setOpen] = useState(false);

  return (
    <div className="mx-auto max-w-5xl">
      {customer.loading ? (
        <Spinner label="Loading customer" />
      ) : customer.error ? (
        <ErrorState detail={customer.error} onRetry={customer.reload} />
      ) : !customer.data ? null : (
        <>
          <PageHeader
            title={customer.data.name}
            subtitle={
              <span className="flex items-center gap-2">
                <Badge tone="brand">{customer.data.code}</Badge>
                <span className="text-xs text-ink-400">tenant-safe: this page shows only your subtree</span>
              </span>
            }
          />

          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Account families</CardTitle>
                {canWrite && <Button size="sm" onClick={() => setOpen(true)}>Add family</Button>}
              </div>
            </CardHeader>
            <CardBody>
              <p className="mb-3 text-xs text-ink-500">
                An account family groups the cloud accounts or subscriptions billed to this customer.
              </p>
              {families.loading ? (
                <Spinner label="Loading families" />
              ) : (families.data?.length ?? 0) === 0 ? (
                <div className="rounded-xl border border-dashed border-ink-200 px-4 py-8 text-center text-xs text-ink-400">
                  No account families yet. Create one before importing billing data (Phase 1).
                </div>
              ) : (
                <ul className="space-y-2" aria-label="Account families">
                  {families.data!.map((f) => (
                    <li key={f.id} className="flex items-center justify-between rounded-lg border border-ink-200 px-3 py-2">
                      <div>
                        <div className="text-sm font-medium">{f.name}</div>
                        {f.description && <div className="text-xs text-ink-400">{f.description}</div>}
                      </div>
                      <Badge tone={f.account_count ? "positive" : "neutral"}>
                        {f.account_count ?? 0} account{f.account_count === 1 ? "" : "s"}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <div className="mt-6">
            <ComingLater feature="Cloud accounts, contracts, usage and invoices for this customer" phase={1} />
          </div>

          {open && (
            <CreateFamilyModal
              customerId={id}
              onClose={() => setOpen(false)}
              onCreated={() => {
                setOpen(false);
                families.reload();
                customer.reload();
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

function CreateFamilyModal({
  customerId,
  onClose,
  onCreated,
}: {
  customerId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/v1/customers/${customerId}/account-families`, {
        name,
        description: description || null,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create family");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open title="Add account family" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Name" required hint="e.g. Production Workloads, Migrated Legacy.">
          {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />}
        </Field>
        <Field label="Description">
          {(id) => <Textarea id={id} value={description} onChange={(e) => setDescription(e.target.value)} />}
        </Field>
        {error && (
          <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" loading={busy}>Create family</Button>
        </div>
      </form>
    </Modal>
  );
}
