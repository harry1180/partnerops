/**
 * @cloudpartnerops/shared — canonical domain types mirrored from the FastAPI
 * OpenAPI schema (apps/api → /api/openapi.json). Keep in sync; the web app
 * consumes the generated client for fetch calls and these types for shapes.
 */

export type OrganizationKind =
  | "platform"
  | "distributor"
  | "reseller"
  | "customer"
  | "internal";

export type ProviderCode = "aws" | "azure" | "gcp";

export type RoleKey =
  | "platform_admin"
  | "distributor_admin"
  | "msp_admin"
  | "finops_analyst"
  | "billing_analyst"
  | "customer_admin"
  | "customer_readonly"
  | "auditor";

export interface Branding {
  product_name: string;
  logo_url: string | null;
  primary_color: string;
  accent_color: string;
  support_email: string | null;
  email_sender_name: string | null;
  custom_domain: string | null;
  terminology: Record<string, string>;
  feature_flags: Record<string, boolean>;
  org_path: string;
}

export interface Organization {
  id: string;
  kind: OrganizationKind;
  name: string;
  legal_name: string | null;
  parent_id: string | null;
  path: string;
  currency: string;
  status: "active" | "suspended" | "archived";
  created_at: string;
}

export interface Customer {
  id: string;
  owning_org_id: string;
  name: string;
  code: string;
  status: "active" | "archived";
  account_family_count?: number;
}

export interface CurrentUser {
  id: string;
  email: string;
  display_name: string;
  org_id: string;
  org_kind: OrganizationKind;
  roles: RoleKey[];
  permissions: string[];
  mfa_enabled: boolean;
  /** Effective scope: org ids this user can read (server-computed). */
  scope_org_ids: string[];
}

export interface HealthStatus {
  status: "ok" | "degraded";
  version: string;
  checks: Record<string, "ok" | "error">;
  correlation_id?: string;
}

export interface ApiListResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/* ---------- money ----------
 * Monetary values cross the wire as strings (Decimal text) and are never
 * parsed into JS floats for arithmetic. Formatting only — actual money math
 * happens in the Python billing engine on NUMERIC types.
 */

export function formatMoney(
  value: string | number,
  currency = "USD",
  opts: { decimals?: number } = {},
): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  const decimals = opts.decimals ?? 2;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(n);
}

export function formatCount(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

export function addMoney(a: string, b: string): string {
  // Integer-cent addition on BigInt — no float drift. Amounts must have ≤ 2
  // decimals; this is used only for UI display aggregation, never for
  // invoicing (invoicing is server-side).
  const cents = (s: string) => {
    const neg = s.trim().startsWith("-");
    const t = s.replace("-", "");
    const [i, f = ""] = t.split(".");
    const v = BigInt(i) * 100n + BigInt((f + "00").slice(0, 2));
    return neg ? -v : v;
  };
  const c = cents(a) + cents(b);
  const sign = c < 0n ? "-" : "";
  const abs = c < 0n ? -c : c;
  return `${sign}${(abs / 100n).toString()}.${(abs % 100n).toString().padStart(2, "0")}`;
}
