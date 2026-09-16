"use client";

import { useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import { api } from "@/lib/api";
import { fetchCatalog, fetchOrgs, fetchUsers, useAsync, type OrgNode, type UserRow } from "@/lib/hooks";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, ErrorState, Field, Input, Modal,
  PageHeader, Select, Spinner, StatusPill, Table, Tabs, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

export default function AdministrationPage() {
  const { me } = useApp();
  const canManage = (me?.permissions.includes("user.manage") ?? false) || (me?.roles.includes("platform_admin") ?? false);

  const tabs = [
    { key: "orgs", label: "Organizations", panel: <OrgTree /> },
    ...(canManage
      ? [
          { key: "users", label: "Users & roles", panel: <UsersPanel /> },
          { key: "tokens", label: "API tokens", panel: <TokensPanel /> },
        ]
      : []),
    { key: "catalog", label: "Roles & permissions", panel: <CatalogPanel /> },
  ];

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Administration"
        subtitle="Organization hierarchy, identities, integration credentials and the permission model in force."
      />
      <Tabs items={tabs} />
    </div>
  );
}

function OrgTree() {
  const orgs = useAsync(fetchOrgs, []);
  const { me } = useApp();
  const canManage = (me?.permissions.includes("org.manage") ?? false) || (me?.roles.includes("platform_admin") ?? false);
  const [open, setOpen] = useState(false);

  if (orgs.loading) return <Spinner label="Loading organizations" />;
  if (orgs.error) return <ErrorState detail={orgs.error} onRetry={orgs.reload} />;
  const rows = orgs.data ?? [];
  const depthOf = (o: OrgNode) => o.path.split("/").filter(Boolean).length;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Organization hierarchy</CardTitle>
          {canManage && <Button size="sm" onClick={() => setOpen(true)}>Add organization</Button>}
        </div>
      </CardHeader>
      <CardBody className="p-0">
        <Table>
          <THead>
            <TR>
              <TH>Organization</TH>
              <TH>Kind</TH>
              <TH>Children</TH>
              <TH>Currency</TH>
              <TH>Status</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((o) => (
              <TR key={o.id}>
                <TD style={{ paddingLeft: `${0.75 + (depthOf(o) - 1) * 1.25}rem` }}>
                  <span className="font-medium">{o.name}</span>
                </TD>
                <TD><Badge tone={o.kind === "platform" ? "brand" : o.kind === "customer" ? "neutral" : "info"}>{o.kind}</Badge></TD>
                <TD className="tabular-nums">{o.child_count ?? 0}</TD>
                <TD>{o.currency}</TD>
                <TD><StatusPill status={o.status} /></TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardBody>
      {open && <CreateOrgModal onClose={() => setOpen(false)} onCreated={() => { setOpen(false); orgs.reload(); }} />}
    </Card>
  );
}

function CreateOrgModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { me } = useApp();
  const isPlatform = me?.roles.includes("platform_admin") ?? false;
  const kinds = isPlatform
    ? ["distributor", "reseller"]
    : ["reseller", "customer"];
  const [kind, setKind] = useState(kinds[0] ?? "customer");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/v1/orgs", { kind, name });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create organization");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open title={`New ${kind} organization`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Kind" required hint="Parent is your own organization.">
          {(id) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)} disabled={!isPlatform}>
              {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Name" required>
          {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />}
        </Field>
        {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" loading={busy}>Create</Button>
        </div>
      </form>
    </Modal>
  );
}

function UsersPanel() {
  const users = useAsync(fetchUsers, []);
  if (users.loading) return <Spinner label="Loading users" />;
  if (users.error) return <ErrorState detail={users.error} onRetry={users.reload} />;
  return (
    <Card>
      <CardHeader><CardTitle>Users in your scope</CardTitle></CardHeader>
      <CardBody className="p-0">
        <Table>
          <THead>
            <TR><TH>User</TH><TH>Email</TH><TH>Roles</TH><TH>Last login</TH><TH>Status</TH></TR>
          </THead>
          <TBody>
            {(users.data ?? []).map((u: UserRow) => (
              <TR key={u.id}>
                <TD className="font-medium">{u.display_name}</TD>
                <TD className="font-mono text-xs">{u.email}</TD>
                <TD>
                  <div className="flex flex-wrap gap-1">
                    {u.roles.map((r) => <Badge key={r} tone="neutral">{r.replace(/_/g, " ")}</Badge>)}
                  </div>
                </TD>
                <TD className="text-xs text-ink-500">
                  {u.last_login_at ? new Date(u.last_login_at).toLocaleString("en-CA", { hour12: false }) : "never"}
                </TD>
                <TD><StatusPill status={u.status} /></TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardBody>
    </Card>
  );
}

function TokensPanel() {
  const [items, setItems] = useState<{ id: string; name: string; scopes: string[]; created_at: string; last_used_at: string | null }[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    api
      .get<typeof items>("/api/v1/admin/tokens")
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Scoped API tokens</CardTitle>
          <Button size="sm" onClick={() => setOpen(true)}>New token</Button>
        </div>
      </CardHeader>
      <CardBody className="p-0">
        {loading ? (
          <div className="p-5"><Spinner label="Loading tokens" /></div>
        ) : items.length === 0 ? (
          <div className="px-5 py-8 text-center text-xs text-ink-400">
            No API tokens yet. Tokens authenticate the REST API (Authorization: Bearer cpo_…) and can be
            rotated without deleting.
          </div>
        ) : (
          <Table>
            <THead><TR><TH>Name</TH><TH>Scopes</TH><TH>Created</TH><TH>Last used</TH><TH /></TR></THead>
            <TBody>
              {items.map((t) => (
                <TR key={t.id}>
                  <TD className="font-medium">{t.name}</TD>
                  <TD className="max-w-sm"><div className="flex flex-wrap gap-1">{t.scopes.map((s) => <Badge key={s} tone="info">{s}</Badge>)}</div></TD>
                  <TD className="text-xs text-ink-500">{new Date(t.created_at).toLocaleDateString()}</TD>
                  <TD className="text-xs text-ink-500">{t.last_used_at ? new Date(t.last_used_at).toLocaleDateString() : "never"}</TD>
                  <TD className="text-right">
                    <Button
                      size="sm" variant="ghost"
                      onClick={async () => {
                        const r = await api.post<{ token: string }>(`/api/v1/admin/tokens/${t.id}/rotate`);
                        setSecret(r.token);
                        reload();
                      }}
                    >
                      Rotate
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardBody>
      {open && <CreateTokenModal onClose={() => setOpen(false)} onCreated={(tok) => { setOpen(false); setSecret(tok); reload(); }} />}
      {secret && (
        <div className="m-4 rounded-lg bg-emerald-50 px-4 py-3 text-xs ring-1 ring-emerald-200">
          <p className="font-medium text-positive">Token created. Copy it now — it is shown only once:</p>
          <code className="mt-1 block break-all font-mono text-[11px] text-ink-800">{secret}</code>
          <div className="mt-2 text-right"><Button size="sm" variant="secondary" onClick={() => setSecret(null)}>Done</Button></div>
        </div>
      )}
    </Card>
  );
}

function CreateTokenModal({ onClose, onCreated }: { onClose: () => void; onCreated: (token: string) => void }) {
  const { me } = useApp();
  const [name, setName] = useState("");
  const [scopeCsv, setScopeCsv] = useState("customer.read, invoice.read");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ token: string }>("/api/v1/admin/tokens", {
        name,
        org_id: me?.org_id,
        scopes: scopeCsv.split(",").map((s) => s.trim()).filter(Boolean),
      });
      onCreated(r.token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create token");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open title="New API token" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Name" required>
          {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />}
        </Field>
        <Field label="Scopes" required hint="Comma-separated permission keys — the token can never exceed your own permissions.">
          {(id) => <Input id={id} value={scopeCsv} onChange={(e) => setScopeCsv(e.target.value)} required className="font-mono text-xs" />}
        </Field>
        {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" loading={busy}>Create token</Button>
        </div>
      </form>
    </Modal>
  );
}

function CatalogPanel() {
  const catalog = useAsync(fetchCatalog, []);
  if (catalog.loading) return <Spinner label="Loading catalog" />;
  if (catalog.error) return <ErrorState detail={catalog.error} onRetry={catalog.reload} />;
  const roles = Object.entries(catalog.data!.roles);
  const perms = catalog.data!.permissions;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>Roles</CardTitle></CardHeader>
        <CardBody className="space-y-2">
          {roles.map(([key, list]) => (
            <div key={key} className="rounded-lg border border-ink-200 px-3 py-2">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-semibold">{key}</span>
                <Badge tone="neutral">{list[0] === "*" ? "all permissions" : `${list.length} perms`}</Badge>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-ink-500">{list.slice(0, 8).join(", ")}{list.length > 8 && !list.includes("*") ? " …" : ""}</p>
            </div>
          ))}
        </CardBody>
      </Card>
      <Card>
        <CardHeader><CardTitle>Permission catalog</CardTitle></CardHeader>
        <CardBody className="max-h-96 overflow-y-auto">
          <ul className="space-y-1.5">
            {Object.entries(perms).map(([k, desc]) => (
              <li key={k}>
                <span className="font-mono text-[11px] font-semibold text-ink-800">{k}</span>
                <span className="ml-2 text-xs text-ink-500">{desc}</span>
              </li>
            ))}
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
