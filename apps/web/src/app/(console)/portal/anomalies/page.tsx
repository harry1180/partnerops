"use client";

import { useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import { api } from "@/lib/api";
import {
  Badge, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, MoneyCell,
  PageHeader, Spinner,
} from "@cloudpartnerops/ui";

interface PortalAnomaly {
  id: string; month: string; service: string | null;
  direction: "increase" | "decrease"; observed: string; baseline: string; currency: string;
}

export default function PortalAnomalies() {
  const { me } = useApp();
  const [rows, setRows] = useState<PortalAnomaly[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.get<{ items: PortalAnomaly[] }>("/api/v1/portal/anomalies")
      .then((r) => setRows(r.items)).catch((e) => setError(String(e?.message ?? e)));
  }, [me?.id]);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Cost Changes"
        subtitle="Months where a service's charges changed materially against your own recent baseline."
      />
      {error ? <ErrorState detail={error} /> : rows === null ? <Spinner label="Loading cost changes" /> : (
        <Card>
          <CardHeader><CardTitle>{rows.length} notable change{rows.length === 1 ? "" : "s"}</CardTitle></CardHeader>
          <CardBody className="p-0">
            {rows.length === 0 ? <div className="p-5"><EmptyState title="No significant cost changes" hint="We surface material month-over-month changes here." /></div> : (
              <ul className="divide-y divide-ink-100">
                {rows.map((a) => (
                  <li key={a.id} className="flex items-center gap-3 px-5 py-3 text-sm">
                    <Badge tone={a.direction === "increase" ? "warning" : "info"}>{a.direction}</Badge>
                    <span className="flex-1 truncate">{a.service ?? "Total account"} · {a.month}</span>
                    <span className="text-xs text-ink-500">was <MoneyCell value={a.baseline} currency={a.currency} /></span>
                    <span className="font-medium"><MoneyCell value={a.observed} currency={a.currency} /></span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  );
}
