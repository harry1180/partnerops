"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { type UsageGroup } from "@/lib/billing-api";
import {
  Card, EmptyState, ErrorState, MoneyCell, PageHeader, Select, Spinner,
} from "@cloudpartnerops/ui";

export default function PortalUsagePage() {
  const [group, setGroup] = useState("service");
  const [items, setItems] = useState<UsageGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.get<{ items: UsageGroup[] }>(`/api/v1/portal/usage/summary?group_by=${group}`)
      .then((r) => setItems(r.items))
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [group]);

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="Usage Explorer"
        subtitle="Your cloud spend, grouped the way your invoices are."
        filters={
          <Select aria-label="Group by" className="w-56" value={group} onChange={(e) => setGroup(e.target.value)}>
            <option value="service">By service</option>
            <option value="cost_category">By category</option>
            <option value="region">By region</option>
            <option value="environment">By environment</option>
            <option value="application">By application</option>
          </Select>
        }
      />
      {loading ? <Spinner label="Loading your usage" /> : error ? <ErrorState detail={error} /> : (
        <Card>
          {(items ?? []).length === 0 ? (
            <EmptyState title="No usage recorded yet" hint="Usage appears here after your provider data is imported and reconciled." />
          ) : (
            <ul className="divide-y divide-ink-100">
              {items!.map((g) => {
                const max = Math.max(...items!.map((x) => Number(x.provider_cost)));
                return (
                  <li key={g.group} className="px-5 py-3">
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-medium">{g.group}</span>
                      <MoneyCell value={g.provider_cost} />
                    </div>
                    <div className="mt-1 h-1.5 rounded bg-ink-100">
                      <div className="h-1.5 rounded bg-brand-primary" style={{ width: `${(Number(g.provider_cost) / max) * 100}%` }} />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}
