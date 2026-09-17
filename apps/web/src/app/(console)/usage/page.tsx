"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchUsage, type UsageGroup } from "@/lib/billing-api";
import {
  Badge, Card, CardBody, CardHeader, CardTitle, ErrorState, MoneyCell, PageHeader, Select,
  Spinner, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const GROUPS = ["service", "cost_category", "region", "environment", "application", "owner"];
const PERIODS = [
  { label: "All periods", value: "" },
  { label: "June 2026", value: "2026-06-01T00:00:00+00:00" },
  { label: "July 2026", value: "2026-07-01T00:00:00+00:00" },
  { label: "August 2026", value: "2026-08-01T00:00:00+00:00" },
];

export default function UsagePage() {
  const [groupBy, setGroupBy] = useState("service");
  const [period, setPeriod] = useState("");
  const [items, setItems] = useState<UsageGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchUsage(groupBy, period || undefined)
      .then((r) => setItems(r.items))
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [groupBy, period]);
  useEffect(load, [load]);

  const total = (items ?? []).reduce((acc, i) => acc + Number(i.provider_cost), 0);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Usage Explorer"
        subtitle="Grouped provider spend from normalized records. The browser never receives raw rows — aggregation is server-side."
        filters={
          <div className="flex flex-wrap gap-2">
            <Select aria-label="Group by" className="w-52" value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
              {GROUPS.map((g) => <option key={g} value={g}>Group: {g.replace(/_/g, " ")}</option>)}
            </Select>
            <Select aria-label="Billing period" className="w-52" value={period} onChange={(e) => setPeriod(e.target.value)}>
              {PERIODS.map((p) => <option key={p.label} value={p.value}>{p.label}</option>)}
            </Select>
          </div>
        }
      />
      {loading ? <Spinner label="Loading usage" /> : error ? <ErrorState detail={error} onRetry={load} /> : (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Provider billed cost — {groupBy}</CardTitle>
              <Badge tone="neutral">Σ ${total.toLocaleString("en-US", { maximumFractionDigits: 0 })}</Badge>
            </div>
          </CardHeader>
          <CardBody className="p-0">
            <Table>
              <THead><TR><TH>Group</TH><TH className="text-right">List cost</TH><TH className="text-right">Provider billed</TH><TH className="text-right">Credits</TH><TH className="text-right">Records</TH></TR></THead>
              <TBody>
                {(items ?? []).map((g) => (
                  <TR key={g.group}>
                    <TD>{g.group}</TD>
                    <TD className="text-right text-xs"><MoneyCell value={g.list_cost} /></TD>
                    <TD className="text-right font-medium"><MoneyCell value={g.provider_cost} /></TD>
                    <TD className="text-right text-xs"><MoneyCell value={g.credit} /></TD>
                    <TD className="text-right tabular-nums text-xs text-ink-500">{g.rows.toLocaleString()}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
