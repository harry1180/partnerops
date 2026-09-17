"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useApp } from "@/lib/app-state";
import { fetchMargins, type MarginSummary } from "@/lib/billing-api";
import {
  Badge, Card, CardBody, CardHeader, CardTitle, ErrorState, MoneyCell, PageHeader, Select,
  Spinner, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const PERIODS = [
  { label: "All periods", value: "" },
  { label: "June 2026", value: "2026-06-01T00:00:00+00:00" },
  { label: "July 2026", value: "2026-07-01T00:00:00+00:00" },
  { label: "August 2026", value: "2026-08-01T00:00:00+00:00" },
];

export default function MarginsPage() {
  const { me } = useApp();
  const allowed = me?.permissions.includes("margin.view") ?? false;
  const [period, setPeriod] = useState("");
  const [data, setData] = useState<MarginSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    if (!allowed) { setLoading(false); return; }
    setLoading(true); setError(null);
    fetchMargins(period || undefined).then(setData)
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [period, allowed]);
  useEffect(load, [load]);

  if (!allowed) {
    return (
      <div className="mx-auto max-w-7xl">
        <PageHeader title="Margins" />
        <Card className="p-6 text-sm text-ink-500">
          Your role does not include <code className="font-mono">margin.view</code>. Partner margin is
          never exposed to customer accounts.
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Margins &amp; Revenue Leakage"
        subtitle="Revenue minus provider cost per customer, with leakage signals. Figures come from pricing runs and canonical costs — no estimates."
        filters={
          <Select aria-label="Period" className="w-52" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => <option key={p.label} value={p.value}>{p.label}</option>)}
          </Select>
        }
      />
      {loading ? <Spinner label="Loading margins" /> : error ? <ErrorState detail={error} onRetry={load} /> : !data ? null : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Card className="px-5 py-4"><div className="text-xs uppercase tracking-wide text-ink-500">Revenue (invoiced)</div><div className="mt-1 font-display text-2xl"><MoneyCell value={data.totals.revenue} /></div></Card>
            <Card className="px-5 py-4"><div className="text-xs uppercase tracking-wide text-ink-500">Provider cost</div><div className="mt-1 font-display text-2xl"><MoneyCell value={data.totals.provider_cost} /></div></Card>
            <Card className="px-5 py-4"><div className="text-xs uppercase tracking-wide text-ink-500">Gross margin</div>
              <div className="mt-1 font-display text-2xl"><MoneyCell value={data.totals.margin} /></div>
              {data.totals.margin_pct !== null && <div className="text-xs text-ink-400">{Number(data.totals.margin_pct).toFixed(1)}%</div>}
            </Card>
            <Card className="px-5 py-4"><div className="text-xs uppercase tracking-wide text-ink-500">Leakage signals</div>
              <div className="mt-1 flex flex-wrap gap-1 text-xs">
                {data.totals.negative_margin_customers > 0 && <Badge tone="negative">{data.totals.negative_margin_customers} negative margin</Badge>}
                {data.totals.low_margin_customers > 0 && <Badge tone="warning">{data.totals.low_margin_customers} below 10%</Badge>}
                {data.totals.unbilled_customers > 0 && <Badge tone="warning">{data.totals.unbilled_customers} unbilled</Badge>}
                {data.totals.negative_margin_customers === 0 && data.totals.low_margin_customers === 0 && data.totals.unbilled_customers === 0 && <Badge tone="positive">none</Badge>}
              </div>
            </Card>
          </div>

          <Card className="mt-4">
            <CardHeader><CardTitle>Margin by customer</CardTitle></CardHeader>
            <CardBody className="p-0">
              <Table>
                <THead><TR><TH>Customer</TH><TH className="text-right">Provider cost</TH><TH className="text-right">Revenue</TH><TH className="text-right">Margin</TH><TH className="text-right">vs target</TH><TH>Signals</TH></TR></THead>
                <TBody>
                  {data.customers.map((c) => {
                    const mpct = c.margin_pct !== null ? Number(c.margin_pct) : null;
                    const below = mpct !== null && c.target_margin_pct !== null && mpct < c.target_margin_pct;
                    return (
                      <TR key={c.customer_id}>
                        <TD><Link href={`/customers/${c.customer_id}`} className="cpo-focus rounded font-medium underline-offset-2 hover:underline">{c.customer}</Link><span className="ml-2 font-mono text-[10px] text-ink-400">{c.code}</span></TD>
                        <TD className="text-right text-xs"><MoneyCell value={c.provider_cost} /></TD>
                        <TD className="text-right text-xs"><MoneyCell value={c.revenue} /></TD>
                        <TD className={`text-right text-sm font-medium ${Number(c.margin) < 0 ? "text-negative" : ""}`}><MoneyCell value={c.margin} /></TD>
                        <TD className="text-right text-xs tabular-nums">
                          {mpct !== null ? (
                            <span className={below ? "text-warning" : "text-positive"}>{mpct.toFixed(1)}%{c.target_margin_pct ? ` / ${c.target_margin_pct}%` : ""}</span>
                          ) : <span className="text-ink-400">—</span>}
                        </TD>
                        <TD className="text-xs">
                          {Number(c.margin) < 0 && <Badge tone="negative">negative</Badge>}
                          {c.unbilled_usage && <Badge tone="warning">unbilled usage</Badge>}
                          {!c.invoiced && !c.unbilled_usage && <Badge tone="neutral">no usage</Badge>}
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </CardBody>
          </Card>
          <p className="mt-3 text-xs text-ink-400">
            Margin = invoiced revenue − provider cost (usage + support + tax as booked by pricing). Charts
            (trend line, per-service breakdown) arrive with the reporting layer; the underlying /margins/trend
            API is live and used by the next phase's ECharts panels.
          </p>
        </>
      )}
    </div>
  );
}
