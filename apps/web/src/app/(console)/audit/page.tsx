"use client";

import { useState } from "react";
import { useApp } from "@/lib/app-state";
import { fetchAudit, useAsync, type AuditRow } from "@/lib/hooks";
import {
  Badge, Card, ErrorState, PageHeader, Pagination, Select, Spinner,
  Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

const ACTION_OPTIONS = [
  "", "login.success", "login.failure", "org.created", "customer.created",
  "account_family.created", "user.created", "user.role_changed", "branding.updated",
  "integration.changed", "seed.demo_data",
];

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("en-CA", { hour12: false });
}

export default function AuditPage() {
  const { me } = useApp();
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const data = useAsync(() => fetchAudit(page, action || undefined), [page, action]);

  if (!me?.permissions.includes("audit.read")) {
    return (
      <div className="mx-auto max-w-7xl">
        <PageHeader title="Audit Trail" />
        <Card className="p-6 text-sm text-ink-500">
          Your role does not include <code className="font-mono text-xs">audit.read</code>. This screen is
          visible to auditors, admins and platform staff only.
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Audit Trail"
        subtitle="Append-only evidence of logins, configuration changes, approvals, exports and calculation runs. Scope: your organization subtree."
        filters={
          <div className="flex items-center gap-2">
            <Select
              aria-label="Filter by action"
              value={action}
              onChange={(e) => {
                setAction(e.target.value);
                setPage(1);
              }}
              className="w-64"
            >
              {ACTION_OPTIONS.map((a) => (
                <option key={a} value={a}>
                  {a === "" ? "All actions" : a}
                </option>
              ))}
            </Select>
            <Badge tone="info">immutable</Badge>
          </div>
        }
      />

      <Card>
        {data.loading ? (
          <div className="p-5">
            <Spinner label="Loading audit events" />
          </div>
        ) : data.error ? (
          <div className="p-5">
            <ErrorState detail={data.error} onRetry={data.reload} />
          </div>
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH>Time (UTC)</TH>
                  <TH>Actor</TH>
                  <TH>Action</TH>
                  <TH>Entity</TH>
                  <TH>Summary</TH>
                  <TH>Correlation</TH>
                </TR>
              </THead>
              <TBody>
                {data.data!.items.map((r: AuditRow) => (
                  <TR key={r.id}>
                    <TD className="whitespace-nowrap text-xs tabular-nums text-ink-500">{fmtTime(r.created_at)}</TD>
                    <TD>
                      <div className="text-xs font-medium">{r.actor_label}</div>
                      <div className="text-[10px] uppercase tracking-wide text-ink-400">{r.actor_kind}</div>
                    </TD>
                    <TD>
                      <code className="rounded bg-ink-100 px-1.5 py-0.5 font-mono text-[11px] text-ink-800">{r.action}</code>
                    </TD>
                    <TD className="text-xs text-ink-500">{r.entity_type ?? "—"}</TD>
                    <TD className="max-w-md text-xs">{r.summary}</TD>
                    <TD className="font-mono text-[10px] text-ink-400">{r.correlation_id ?? "—"}</TD>
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
    </div>
  );
}
