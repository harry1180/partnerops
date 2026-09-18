"use client";

import { useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import { api } from "@/lib/api";
import {
  Badge, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, MoneyCell,
  PageHeader, Spinner, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

interface PortalBudget {
  id: string; name: string; period_start: string; period_end: string;
  amount: string; actual: string; currency: string; pct_of_budget: string;
  over_budget: boolean;
}

export default function PortalBudgets() {
  const { me } = useApp();
  const [rows, setRows] = useState<PortalBudget[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.get<{ items: PortalBudget[] }>("/api/v1/portal/budgets")
      .then((r) => setRows(r.items)).catch((e) => setError(String(e?.message ?? e)));
  }, [me?.id]);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Budgets" subtitle="Your spend vs the budgets your partner set for your account." />
      {error ? <ErrorState detail={error} /> : rows === null ? <Spinner label="Loading budgets" /> : (
        <Card>
          <CardHeader><CardTitle>{rows.length} budget{rows.length === 1 ? "" : "s"}</CardTitle></CardHeader>
          <CardBody className="p-0">
            {rows.length === 0 ? <div className="p-5"><EmptyState title="No budgets configured" /></div> : (
              <Table>
                <THead><TR><TH>Budget</TH><TH>Period</TH><TH className="text-right">Your charges</TH><TH className="text-right">Budget</TH><TH>Status</TH></TR></THead>
                <TBody>
                  {rows.map((b) => (
                    <TR key={b.id}>
                      <TD className="text-xs font-medium">{b.name}</TD>
                      <TD className="text-xs">{b.period_start.slice(0, 7)} – {b.period_end.slice(0, 7)}</TD>
                      <TD className="text-right text-xs"><MoneyCell value={b.actual} currency={b.currency} /></TD>
                      <TD className="text-right text-xs"><MoneyCell value={b.amount} currency={b.currency} /></TD>
                      <TD>
                        {b.over_budget
                          ? <Badge tone="warning">over budget ({b.pct_of_budget}%)</Badge>
                          : <Badge tone="positive">{b.pct_of_budget}% used</Badge>}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  );
}
