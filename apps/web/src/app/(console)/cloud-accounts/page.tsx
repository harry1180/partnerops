"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import { api } from "@/lib/api";
import { loadSynthetic } from "@/lib/billing-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, Field,
  Modal, Input, MoneyCell, PageHeader, Pagination, Select, Spinner, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

interface AccountRow {
  id: string; provider: string; external_id: string; name: string;
  allocation_status: string; account_kind: string | null;
  family_name: string | null; customer_code: string | null; customer_name: string | null;
  lifetime_usage_cost: string;
}

export default function CloudAccountsPage() {
  const { me } = useApp();
  const canRead = me?.permissions.includes("cost.read") ?? false;
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ items: AccountRow[]; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const canWrite = me?.permissions.includes("customer.write") ?? false;
  const [mapTarget, setMapTarget] = useState<AccountRow | null>(null);

  const load = useCallback(() => {
    if (!canRead) { setLoading(false); return; }
    setLoading(true); setError(null);
    const qs = new URLSearchParams({ page: String(page), page_size: "50" });
    if (statusFilter) qs.set("allocation_status", statusFilter);
    api.get<{ items: AccountRow[]; total: number }>(`/api/v1/cloud-accounts?${qs}`)
      .then(setData).catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [canRead, page, statusFilter]);
  useEffect(load, [load]);
  useEffect(() => setPage(1), [statusFilter]);

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Cloud Accounts"
        subtitle="Linked AWS accounts / subscriptions in your book of business. Unmapped accounts are the #1 cause of unbilled usage."
        actions={canWrite ? <ImportPanel onDone={load} /> : undefined}
        filters={
          <Select aria-label="Allocation status" className="w-52" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All accounts</option>
            <option value="mapped">Mapped</option>
            <option value="unmapped">Unmapped</option>
            <option value="excluded">Excluded</option>
          </Select>
        }
      />
      {error ? (
        <Card className="p-4"><ErrorState detail={error} onRetry={load} /></Card>
      ) : loading ? <Spinner label="Loading accounts" /> : !data || data.items.length === 0 ? (
        <Card className="p-5"><EmptyState title="No accounts match" hint="Accounts appear here automatically when provider billing data is ingested, then map them to a customer account family." /></Card>
      ) : (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>{data.total.toLocaleString()} account{data.total === 1 ? "" : "s"}</CardTitle>
              {statusFilter === "" && data.items.some((i) => i.allocation_status === "unmapped") && (
                <Button size="sm" variant="secondary" onClick={() => setStatusFilter("unmapped")}>
                  Review unmapped
                </Button>
              )}
            </div>
          </CardHeader>
          <CardBody className="p-0">
            <Table>
              <THead><TR><TH>Account</TH><TH>Provider</TH><TH>Customer / family</TH><TH>Allocation</TH><TH className="text-right">Lifetime usage</TH><TH /></TR></THead>
              <TBody>
                {data.items.map((a) => (
                  <TR key={a.id}>
                    <TD>
                      <span className="font-medium">{a.name}</span>
                      <span className="ml-2 font-mono text-[10px] text-ink-400">{a.external_id}</span>
                    </TD>
                    <TD><Badge tone="neutral">{a.provider.toUpperCase()}</Badge>{a.account_kind === "payer" && <Badge tone="info">payer</Badge>}</TD>
                    <TD className="text-xs">{a.customer_name ? `${a.customer_name} · ${a.family_name ?? "no family"}` : <span className="text-ink-400">not attributed</span>}</TD>
                    <TD>
                      <Badge tone={a.allocation_status === "mapped" ? "positive" : a.allocation_status === "unmapped" ? "warning" : "neutral"}>
                        {a.allocation_status}
                      </Badge>
                    </TD>
                    <TD className="text-right text-xs"><MoneyCell value={a.lifetime_usage_cost} /></TD>
                    <TD className="text-right">
                      {canWrite && (
                        <Button size="sm" variant="ghost" onClick={() => setMapTarget(a)}>
                          {a.allocation_status === "mapped" ? "Re-map" : "Map"}
                        </Button>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <div className="px-3">
              <Pagination page={page} pageSize={50} total={data.total} onPage={setPage} />
            </div>
          </CardBody>
        </Card>
      )}
      {mapTarget && <MapAccountModal account={mapTarget} onClose={() => setMapTarget(null)}
        onDone={() => { setMapTarget(null); load(); }} />}
    </div>
  );
}


interface FamilyOpt { id: string; name: string; customer_code: string; customer_name: string }

function MapAccountModal({ account, onClose, onDone }: {
  account: AccountRow; onClose: () => void; onDone: () => void;
}) {
  const [families, setFamilies] = useState<FamilyOpt[] | null>(null);
  const [familyId, setFamilyId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<FamilyOpt[]>("/api/v1/account-families")
      .then((rs) => { setFamilies(rs); setFamilyId((cur) => cur || (rs[0]?.id ?? "")); })
      .catch((e) => setError(String(e?.message ?? e)));
  }, []);

  async function submit(exclude: boolean) {
    setBusy(true); setError(null);
    try {
      await api.patch(`/api/v1/cloud-accounts/${account.id}`, exclude
        ? { exclude: true } : { account_family_id: familyId });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "mapping failed");
    } finally { setBusy(false); }
  }

  return (
    <Modal open title={`Map ${account.name} (${account.external_id})`} onClose={onClose}>
      <p className="mb-3 text-xs text-ink-500">
        Ingested costs for this account will be attributed to the chosen customer family on the next ingestion or pricing run. Provider-side mapping happens automatically for accounts already present in billing data.
      </p>
      {families === null && !error ? <Spinner label="Loading families" /> : (
        <>
          {families!.length === 0 && (
            <p className="mb-3 text-xs text-warning">No account families exist yet — create one on a customer page first.</p>
          )}
          <Field label="Account family">
            {(id) => (
              <Select id={id} value={familyId} onChange={(e) => setFamilyId(e.target.value)}>
                {families!.map((f) => (
                  <option key={f.id} value={f.id}>{f.customer_name} · {f.name}</option>
                ))}
              </Select>
            )}
          </Field>
        </>
      )}
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
      <div className="mt-4 flex justify-between gap-2">
        <Button variant="secondary" loading={busy} disabled={busy} onClick={() => submit(true)}>
          Exclude from billing
        </Button>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button loading={busy} disabled={!familyId} onClick={() => submit(false)}>Map</Button>
        </div>
      </div>
    </Modal>
  );
}


function ImportPanel({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [months, setMonths] = useState("2026-06,2026-07,2026-08");

  return (
    <Card className="w-full max-w-md">
      <CardBody>
        <Field label="Demo billing months" hint="Deterministic synthetic AWS CUR fixtures.">
          {(id) => (
            <Input id={id} value={months} onChange={(e) => setMonths(e.target.value)} className="font-mono text-xs" />
          )}
        </Field>
        <div className="mt-2 flex gap-2">
          <Button size="sm" loading={busy} onClick={async () => {
            setBusy(true); setError(null); setMsg(null);
            try {
              const res = await loadSynthetic(months);
              const r = res.results;
              setMsg(`${r.reduce((a, x) => a + x.canonical, 0)} cost records across ${r.length} files`
                + (r.some((x) => x.skipped_duplicate_file) ? " (some skipped as already imported)" : "")
                + (r.some((x) => x.duplicates > 0) ? `; ${r.reduce((a, x) => a + x.duplicates, 0)} duplicates quarantined` : "")
                + (r.some((x) => x.unmapped_accounts.length) ? `; unmapped: ${[...new Set(r.flatMap((x) => x.unmapped_accounts))].join(", ")}` : ""));
              onDone();
            } catch (e) { setError(e instanceof Error ? e.message : "import failed"); }
            finally { setBusy(false); }
          }}>Import synthetic AWS data</Button>
        </div>
        {msg && <p className="mt-2 text-xs text-ink-500">{msg}</p>}
        {error && <p role="alert" className="mt-2 text-xs text-negative">{error}</p>}
      </CardBody>
    </Card>
  );
}
