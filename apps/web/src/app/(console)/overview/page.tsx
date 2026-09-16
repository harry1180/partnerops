"use client";

import { useApp } from "@/lib/app-state";
import { Badge, Card, CardBody, CardHeader, CardTitle, ComingLater, PageHeader, Spinner } from "@cloudpartnerops/ui";
import { fetchOrgs, fetchCustomers, useAsync } from "@/lib/hooks";

/**
 * Executive Overview (Phase 0).
 * Real, live counts come from organizations/customers APIs.
 * Financial panels arrive with Phase 1 pricing (nothing is faked here).
 */
export default function OverviewPage() {
  const { me, capabilities } = useApp();
  const orgs = useAsync(fetchOrgs, []);
  const customers = useAsync(() => fetchCustomers(1), []);

  const orgRows = orgs.data ?? [];
  const counts = {
    distributors: orgRows.filter((o) => o.kind === "distributor").length,
    resellers: orgRows.filter((o) => o.kind === "reseller").length,
    customers: orgRows.filter((o) => o.kind === "customer").length,
  };

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Executive Overview"
        subtitle={
          me
            ? `Signed in as ${me.display_name} · ${me.roles.map((r) => r.replace(/_/g, " ")).join(", ")}`
            : undefined
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="px-5 py-4">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-500">Workspace</div>
          <div className="mt-1 font-display text-lg font-semibold">{me?.org_kind ?? "—"}</div>
          <div className="mt-1 text-xs text-ink-400">{me?.email}</div>
        </Card>
        <Card className="px-5 py-4">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-500">Partner organizations</div>
          <div className="mt-1 font-display text-2xl font-semibold tabular-nums">
            {orgs.loading ? <Spinner label="loading" /> : counts.distributors + counts.resellers}
          </div>
          <div className="mt-1 text-xs text-ink-400">
            {counts.distributors} distributor · {counts.resellers} reseller/MSP in your scope
          </div>
        </Card>
        <Card className="px-5 py-4">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-500">Customers</div>
          <div className="mt-1 font-display text-2xl font-semibold tabular-nums">
            {customers.loading ? <Spinner label="loading" /> : customers.data?.total ?? 0}
          </div>
          <div className="mt-1 text-xs text-ink-400">customer organization nodes in scope</div>
        </Card>
        <Card className="px-5 py-4">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-500">Platform version</div>
          <div className="mt-1 font-display text-2xl font-semibold">{capabilities?.product_version ?? "—"}</div>
          <div className="mt-1 text-xs text-ink-400">{capabilities?.permissions.length ?? 0} permissions granted</div>
        </Card>
      </div>

      <div className="mt-6">
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Billing &amp; FinOps summary</CardTitle>
              <Badge tone="neutral">Coming later</Badge>
            </div>
          </CardHeader>
          <CardBody className="space-y-3">
            <p className="text-sm text-ink-500">
              Revenue, provider cost, gross margin, savings, unbilled usage and anomaly panels appear here
              once Phase 1 (AWS billing MVP) lands the ingestion → pricing → invoice pipeline. They will be
              computed from real canonical cost records — never placeholders.
            </p>
            <ComingLater feature="Margin, spend and forecast charts" phase={1} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
