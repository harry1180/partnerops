"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useApp } from "@/lib/app-state";
import { fetchInvoices, type InvoicePage } from "@/lib/billing-api";
import {
  Badge, Card, EmptyState, ErrorState, MoneyCell, PageHeader, Pagination, Spinner, StatusPill,
  Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

export default function InvoicesPage() {
  const { me } = useApp();
  const [page, setPage] = useState(1);
  const [data, setData] = useState<InvoicePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = (p: number) => {
    setLoading(true);
    setError(null);
    fetchInvoices(p)
      .then(setData)
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    load(page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const canSeeMargin = me?.permissions.includes("margin.view") ?? false;

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Invoices"
        subtitle="Every amount traces to a pricing run and source records. Issued invoices are immutable."
        actions={<Badge tone="info">period: 2026-06 → 2026-08 demo data</Badge>}
      />
      {loading ? (
        <Card className="p-5"><Spinner label="Loading invoices" /></Card>
      ) : error ? (
        <Card className="p-5"><ErrorState detail={error} onRetry={() => load(page)} /></Card>
      ) : (data?.items.length ?? 0) === 0 ? (
        <Card className="p-5"><EmptyState title="No invoices yet" hint="Run pricing for a customer and create an invoice from the completed run." /></Card>
      ) : (
        <Card>
          <Table>
            <THead><TR>
              <TH>Invoice</TH><TH>Period</TH><TH>Status</TH>
              <TH className="text-right">Total</TH>
              {canSeeMargin && <TH className="text-right">Margin</TH>}
              <TH>Currency</TH>
            </TR></THead>
            <TBody>
              {data!.items.map((i) => (
                <TR key={i.id}>
                  <TD>
                    <Link href={`/invoices/${i.id}`} className="cpo-focus rounded font-mono text-xs underline-offset-2 hover:underline">
                      {i.invoice_number}
                    </Link>
                  </TD>
                  <TD className="text-xs tabular-nums">{i.period_start.slice(0, 10)} → {i.period_end.slice(0, 10)}</TD>
                  <TD><StatusPill status={i.status} /></TD>
                  <TD className="text-right"><MoneyCell value={i.total} currency={i.currency} /></TD>
                  {canSeeMargin && <TD className="text-right"><MoneyCell value={i.margin_total} currency={i.currency} /></TD>}
                  <TD>{i.currency}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <div className="px-3">
            <Pagination page={data!.page} pageSize={data!.page_size} total={data!.total}
                        onPage={(p) => { setPage(p); load(p); }} />
          </div>
        </Card>
      )}
    </div>
  );
}
