"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import {
  checkIntegration, createIntegration, createWebhook, fetchDeliveries, fetchOverview,
  testWebhook, type DeliveryRow, type Overview,
} from "@/lib/integrations-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, Field,
  Input, Modal, PageHeader, Select, Table, TBody, TD, TH, THead, TR, Tabs,
} from "@cloudpartnerops/ui";

const KINDS = ["erp", "accounting", "marketplace", "payment", "servicenow",
  "slack", "teams", "email", "s3_export", "sftp_export"];

export default function IntegrationsPage() {
  const { me } = useApp();
  const canManage = me?.permissions.includes("integration.manage") ?? false;
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [showWh, setShowWh] = useState(false);
  const [showInt, setShowInt] = useState(false);
  const [deliveries, setDeliveries] = useState<{ id: string; items: DeliveryRow[] } | null>(null);

  const load = useCallback(() => {
    if (!canManage) return;
    fetchOverview().then(setData).catch((e) => setError(String(e?.message ?? e)));
  }, [canManage]);
  useEffect(load, [load]);

  if (!canManage) {
    return <EmptyState title="Integrations not available"
      hint="Your role does not include the integration.manage permission." />;
  }
  if (!data) {
    return error ? <ErrorState detail={error} /> : <div className="p-8 text-sm text-ink-500">Loading…</div>;
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <PageHeader
        title="Integrations"
        subtitle="Webhooks, external systems, and scoped API tokens. Every configured boundary reports its real transport state."
        actions={<Badge tone={data.pending_deliveries > 0 ? "warning" : "neutral"}>
          {data.pending_deliveries} deliveries pending
        </Badge>}
      />
      {notice && <p className="rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p>}
      {error && <ErrorState detail={error} />}

      <Tabs items={[
        { key: "webhooks", label: `Webhooks (${data.webhook_endpoints.length})`, panel:
          <Card>
            <CardHeader><CardTitle>Webhook endpoints</CardTitle>
              <Button size="sm" onClick={() => setShowWh(true)}>Add endpoint</Button></CardHeader>
            <CardBody className="p-0">
              {data.webhook_endpoints.length === 0 ? (
                <div className="px-5 py-8 text-center text-sm text-ink-500">
                  No endpoints yet — invoice, dispute, report, and budget events will queue deliveries here.
                </div>
              ) : (
                <Table>
                  <THead><TR><TH>URL</TH><TH>Events</TH><TH>Status</TH><TH>Actions</TH></TR></THead>
                  <TBody>
                    {data.webhook_endpoints.map((e) => (
                      <TR key={e.id}>
                        <TD className="max-w-sm truncate font-mono text-xs">{e.url}</TD>
                        <TD className="text-xs">{e.events.join(", ")}</TD>
                        <TD><Badge tone={e.status === "active" ? "positive" : "neutral"}>{e.status}</Badge></TD>
                        <TD>
                          <div className="flex gap-1">
                            <Button size="sm" variant="secondary" onClick={async () => {
                              setNotice(null);
                              try {
                                const r = await testWebhook(e.id);
                                const last = r.latest[0];
                                setNotice(`Test: queued ${r.queued}, attempted ${r.attempts_this_call}`
                                  + (last ? ` — latest ${last.status}`
                                    + (last.response_code ? ` (HTTP ${last.response_code})` : "")
                                    + (last.error ? ` — ${last.error}` : "") : ""));
                              } catch (err) { setError(String(err instanceof Error ? err.message : err)); }
                            }}>Send test</Button>
                            <Button size="sm" variant="ghost" onClick={async () => {
                              const r = await fetchDeliveries(e.id);
                              setDeliveries({ id: e.id, items: r.items });
                            }}>Deliveries</Button>
                          </div>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </CardBody>
          </Card> },
        { key: "systems", label: `External systems (${data.integrations.length})`, panel:
          <Card>
            <CardHeader><CardTitle>Configured systems</CardTitle>
              <Button size="sm" onClick={() => setShowInt(true)}>Add system</Button></CardHeader>
            <CardBody className="p-0">
              {data.integrations.length === 0 ? (
                <div className="px-5 py-8 text-center text-sm text-ink-500">
                  Nothing configured. ERP/accounting exports run from the invoice page;
                  systems listed here record what each transport is wired to.
                </div>
              ) : (
                <Table>
                  <THead><TR><TH>Name</TH><TH>Kind</TH><TH>Status</TH><TH>Transport</TH><TH /></TR></THead>
                  <TBody>
                    {data.integrations.map((i) => (
                      <TR key={i.id}>
                        <TD>{i.name}</TD>
                        <TD><Badge tone="neutral">{i.kind}</Badge></TD>
                        <TD><Badge tone={i.status === "configured" ? "warning" : "neutral"}>{i.status}</Badge></TD>
                        <TD>{i.connected
                          ? <Badge tone="positive">connected</Badge>
                          : <span className="text-xs text-ink-500">not connected{
                              i.last_check_at ? ` (last check ${new Date(i.last_check_at).toLocaleString()})` : ""
                            }</span>}</TD>
                        <TD><Button size="sm" variant="ghost" onClick={async () => {
                          try {
                            const r = await checkIntegration(i.id);
                            setNotice(`'${i.name}' check: ${r.connected ? "ok" : r.detail}`);
                            load();
                          } catch (e2) { setError(String(e2 instanceof Error ? e2.message : e2)); }
                        }}>Check</Button></TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </CardBody>
          </Card> },
        { key: "tokens", label: "API tokens", panel:
          <Card>
            <CardHeader><CardTitle>Scoped API tokens</CardTitle></CardHeader>
            <CardBody className="space-y-2 text-sm text-ink-700">
              <p>
                Machine-to-machine tokens authenticate with{" "}
                <code className="rounded bg-ink-100 px-1">Authorization: Bearer ***</code>{" "}
                and carry scopes that intersect (never widen) the creator&apos;s permissions.
                The token itself is shown once at creation; only its hash is stored.
              </p>
              <p className="text-ink-500">
                Manage tokens in Administration → API tokens
                {" "}(requires the api_token.manage permission).
              </p>
            </CardBody>
          </Card> },
      ]} />

      {showWh && (
        <WebhookModal events={data.event_types} onClose={() => setShowWh(false)}
          onCreated={(s) => { setSecret(s); setShowWh(false); load(); }} />
      )}
      {secret && (
        <Modal open onClose={() => setSecret(null)} title="Signing secret — shown once">
          <div className="space-y-3">
            <p className="text-sm text-ink-600">
              Deliveries sign their body as{" "}
              <code className="rounded bg-ink-100 px-1">sha256=HMAC(secret, timestamp + &quot;.&quot; + body)</code>{" "}
              in <code className="rounded bg-ink-100 px-1">X-CPPartnerOps-Signature</code>.
              Verify it receiver-side before trusting any payload.
            </p>
            <Input readOnly value={secret} onFocus={(e) => e.currentTarget.select()} />
            <Button onClick={() => { try { void navigator.clipboard?.writeText(secret); } catch { /* headless */ } }}>Copy</Button>
          </div>
        </Modal>
      )}
      {showInt && (
        <IntegrationModal onClose={() => setShowInt(false)} onCreated={() => { setShowInt(false); load(); }} />
      )}
      {deliveries && (
        <Modal open onClose={() => setDeliveries(null)} title="Recent deliveries">
          {deliveries.items.length === 0 ? (
            <p className="text-sm text-ink-500">No deliveries for this endpoint yet.</p>
          ) : (
            <Table>
              <THead><TR><TH>Event</TH><TH>Status</TH><TH>HTTP</TH><TH>Att.</TH><TH>When</TH></TR></THead>
              <TBody>
                {deliveries.items.map((d) => (
                  <TR key={d.id}>
                    <TD className="text-xs">{d.event}</TD>
                    <TD><Badge tone={d.status === "sent" ? "positive" : d.status === "failed" ? "negative" : "neutral"}>
                      {d.status}</Badge></TD>
                    <TD>{d.response_code ?? "—"}</TD>
                    <TD>{d.attempts}</TD>
                    <TD className="text-xs">{d.delivered_at ? new Date(d.delivered_at).toLocaleString()
                      : d.error ?? new Date(d.created_at ?? "").toLocaleString()}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Modal>
      )}
    </div>
  );
}

function WebhookModal({ events, onClose, onCreated }: {
  events: string[]; onClose: () => void; onCreated: (secret: string) => void;
}) {
  const [url, setUrl] = useState("http://127.0.0.1:9999/hooks/billing");
  const [picked, setPicked] = useState<string[]>(["invoice.issued"]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal open onClose={onClose} title="Add webhook endpoint">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setErr(null);
        try {
          const r = await createWebhook({ url, events: picked });
          onCreated(r.signing_secret);
        } catch (e2) {
          setErr(e2 instanceof Error ? e2.message : "could not create endpoint");
        } finally { setBusy(false); }
      }}>
        <Field label="Target URL" hint="https/http only; private/loopback targets are allowed in local deployments.">
          {(id) => <Input id={id} value={url} onChange={(e) => setUrl(e.target.value)} />}
        </Field>
        <div>
          <div className="mb-1 text-sm font-medium">Events</div>
          <div className="grid grid-cols-2 gap-1">
            {events.map((ev) => (
              <label key={ev} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={picked.includes(ev)}
                  onChange={(e) => setPicked((p) => e.target.checked ? [...p, ev] : p.filter((x) => x !== ev))} />
                {ev}
              </label>
            ))}
          </div>
        </div>
        {err && <p className="text-sm text-red-700">{err}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={busy || picked.length === 0}>Create</Button>
        </div>
      </form>
    </Modal>
  );
}

function IntegrationModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [kind, setKind] = useState("erp");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal open onClose={onClose} title="Configure external system">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        try {
          await createIntegration({ kind, name,
            config: url.trim() ? { webhook_url: url.trim() } : {} });
          onCreated();
        } catch (e2) { setErr(e2 instanceof Error ? e2.message : "could not save"); }
      }}>
        <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required />}</Field>
        <Field label="Kind">{(id) => (
          <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </Select>)}</Field>
        <Field label="Endpoint / webhook URL" hint="Optional. Used by the transport check for slack/teams; other kinds need credentials in deployment config.">
          {(id) => <Input id={id} value={url} onChange={(e) => setUrl(e.target.value)} />}
        </Field>
        {err && <p className="text-sm text-red-700">{err}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={name.length < 2}>Save</Button>
        </div>
      </form>
    </Modal>
  );
}
