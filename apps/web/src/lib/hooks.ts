/** Data hooks for the console pages (Phase 0/1). */

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export interface OrgNode {
  id: string;
  kind: string;
  name: string;
  path: string;
  currency: string;
  status: string;
  child_count: number;
}

export interface CustomerRow {
  id: string;
  code: string;
  name: string;
  status: string;
  owning_org_id: string;
  account_family_count: number | null;
  target_margin_pct: number | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export function useAsync<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    fetcher()
      .then(setData)
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, loading, reload };
}

export const fetchOrgs = () => api.get<OrgNode[]>("/api/v1/orgs");
export const fetchCustomers = (page = 1, search?: string) => {
  const params = new URLSearchParams({ page: String(page), page_size: "25" });
  if (search) params.set("search", search);
  return api.get<Page<CustomerRow>>(`/api/v1/customers?${params}`);
};

export interface AuditRow {
  id: string;
  created_at: string;
  actor_kind: string;
  actor_label: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  summary: string;
  correlation_id: string | null;
}

export const fetchAudit = (page = 1, action?: string) => {
  const params = new URLSearchParams({ page: String(page), page_size: "50" });
  if (action) params.set("action", action);
  return api.get<Page<AuditRow>>(`/api/v1/audit-events?${params}`);
};

export interface Catalog {
  permissions: Record<string, string>;
  roles: Record<string, string[]>;
}
export const fetchCatalog = () => api.get<Catalog>("/api/v1/admin/catalog");

export interface UserRow {
  id: string;
  email: string;
  display_name: string;
  org_id: string;
  status: string;
  roles: string[];
  last_login_at: string | null;
}
export const fetchUsers = () => api.get<UserRow[]>("/api/v1/users");
