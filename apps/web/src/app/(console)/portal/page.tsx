"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useApp } from "@/lib/app-state";
import { api } from "@/lib/api";
import {
  Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, MoneyCell,
  PageHeader, Spinner, StatusPill,
} from "@cloudpartnerops/ui";

interface Me { id: string; name: string; code: string }
interface Group { group: string; cost: string }
interface Inv { id: string; invoice_number: string; total: string; currency: string; status: string; period_start: string }

export default function PortalHome() {
  const { me } = useApp();
  const [customer, setCustomer] = useState<Me | null>(null);
  const [usage, setUsage] = useState<Group[] | null>(null);
  const [invoices, setInvoices] = useState<Inv[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.get<Me>("/api/v1/portal/customer"),
      api.get<{ items: Group[] }>("/api/v1/portal/usage/summary?group_by=cost_category"),
      api.get<Inv[]>("/api/v1/portal/invoices"),
    ])
      .then(([c, u, i]) => { setCustomer(c); setUsage(u.items); setInvoices(i); })
      .catch((e) => setError(String(e?.message ?? e)));
  }, [me?.id]);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title={customer ? `Welcome, ${customer.name}` : "Cost Overview"}
        subtitle={customer ? `Customer account ${customer.code}` : undefined}
      />
      {error ? <ErrorState detail={error} /> : !customer ? <Spinner label="Loading your workspace" /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader><CardTitle>Recent charges by category</CardTitle></CardHeader>
            <CardBody>
              {(usage ?? []).length === 0 ? <EmptyState title="No usage yet" /> : (
                <ul className="space-y-2 text-sm">
                  {usage!.slice(0, 6).map((g) => (
                    <li key={g.group} className="flex justify-between">
                      <span>{g.group}</span>
                      <MoneyCell value={g.cost} />
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-3 text-right">
                <Link href="/portal/usage" className="cpo-focus rounded text-xs text-brand-primary underline-offset-2 hover:underline">Open usage explorer →</Link>
              </div>
            </CardBody>
          </Card>
          <Card>
            <CardHeader><CardTitle>Latest invoices</CardTitle></CardHeader>
            <CardBody>
              {(invoices ?? []).length === 0 ? <EmptyState title="No invoices issued yet" /> : (
                <ul className="space-y-2 text-sm">
                  {invoices!.slice(0, 4).map((i) => (
                    <li key={i.id} className="flex items-center justify-between">
                      <span className="flex items-center gap-2">
                        <Link href={`/portal/invoices/${i.id}`} className="cpo-focus rounded font-mono text-xs underline-offset-2 hover:underline">{i.invoice_number}</Link>
                        <StatusPill status={i.status} />
                      </span>
                      <MoneyCell value={i.total} currency={i.currency} />
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-3 text-right">
                <Link href="/portal/invoices" className="cpo-focus rounded text-xs text-brand-primary underline-offset-2 hover:underline">All invoices →</Link>
              </div>
            </CardBody>
          </Card>
        </div>
      )}
    </div>
  );
}
