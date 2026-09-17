/** Phase 1 API hooks (billing pipeline). Money fields are Decimal strings. */

import { api } from "@/lib/api";

export interface ContractRow {
  id: string; code: string; name: string; status: string; customer_id: string;
  versions: {
    id: string; version_number: number; status: string; effective_start: string;
    effective_end: string | null; currency: string; pricing_basis: string;
    rule_bindings: { rule_id: string; rule_version_id: string; order: number }[];
  }[];
}
export const fetchContracts = (customerId?: string) =>
  api.get<ContractRow[]>(`/api/v1/contracts${customerId ? `?customer_id=${customerId}` : ""}`);

export interface RuleVersion {
  id: string; version_number: number; status: string; priority: number; calc_order: number;
  applied_basis: string; filters: Record<string, unknown>; parameters: Record<string, unknown>;
  high_impact: boolean; estimated_impact_monthly: string | null;
}
export interface RuleRow {
  id: string; code: string; name: string; rule_type: string; contract_id: string | null;
  status?: string;
  versions: RuleVersion[];
}
export const fetchRules = () => api.get<RuleRow[]>("/api/v1/billing-rules");

export interface PricingTotals {
  provider_cost?: string; support_fee?: string; tax?: string;
  customer_subtotal?: string; credit_amount?: string; margin?: string;
}
export interface PricingRunItem {
  item_number: number; group_key: string; group_label: string; line_kind: string;
  input_amount: string | null; output_amount: string; provider_cost_amount: string;
  formula: string; rule_version_ids: string[]; trace: unknown[];
}
export interface PricingRun {
  id: string; status: string; run_number: number; period_start: string; period_end: string;
  engine_version: string; totals: PricingTotals; supersedes_id: string | null;
  items: PricingRunItem[];
}
export const fetchRun = (id: string) => api.get<PricingRun>(`/api/v1/pricing/runs/${id}`);

export const startPricing = (body: {
  customer_id: string; contract_version_id: string; period_start: string; period_end: string;
}) => api.post<{ run_id: string; status: string; totals: PricingTotals }>("/api/v1/pricing/runs", body);

export const createInvoice = (run_id: string) =>
  api.post<{ id: string; invoice_number: string; status: string; total: string; currency: string }>(
    "/api/v1/invoices", { run_id });

export interface InvoiceRow {
  id: string; invoice_number: string; customer_id: string; status: string;
  total: string; currency: string; period_start: string; period_end: string;
  margin_total: string | null;
}
export interface InvoicePage { items: InvoiceRow[]; total: number; page: number; page_size: number }
export const fetchInvoices = (page = 1, customerId?: string) => {
  const qs = new URLSearchParams({ page: String(page), page_size: "25" });
  if (customerId) qs.set("customer_id", customerId);
  return api.get<InvoicePage>(`/api/v1/invoices?${qs}`);
};

export interface InvoiceDetail {
  id: string; invoice_number: string; customer_id: string; status: string;
  currency: string; payment_terms: string; period_start: string; period_end: string;
  subtotal: string; discounts_total: string; credits_total: string; fees_total: string;
  adjustments_total: string; taxes_total: string; prior_period_adjustments_total: string;
  total: string; provider_cost_total: string | null; margin_total: string | null;
  notes_customer: string | null; notes_internal: string | null;
  issued_at: string | null; due_date: string | null;
  lines: {
    line_number: number; kind: string; group_key: string; description: string;
    quantity: string | null; amount: string; customer_visible: boolean;
    source_record_ids: string[]; rule_version_ids: string[];
    pricing_run_item_id: string | null;
  }[];
}
export const fetchInvoice = (id: string) => api.get<InvoiceDetail>(`/api/v1/invoices/${id}`);
export const transitionInvoice = (id: string, to_status: string, note?: string) =>
  api.post<{ ok: boolean; status: string }>(`/api/v1/invoices/${id}/transition`, { to_status, note });
export const invoiceLineage = (id: string, line: number) =>
  api.get<{
    line_number: number; description: string; amount: string; source_record_ids: string[];
    rule_version_ids: string[]; formula: string | null; calculation_trace: {
      rule: string; v: number; action: string; input: string; output: string; delta: string; formula: string;
    }[]; engine_version: string | null; run_number: number | null;
    contract_version_id: string; provider_cost_amount: string | null;
  }>(`/api/v1/invoices/${id}/lineage?line_number=${line}`);

export interface ReconException {
  id: string; type: string; materiality: string; severity: string; status: string;
  amount_delta: string; explanation: string; customer_id: string | null;
  billing_account_ref: string | null; evidence: Record<string, string>; run_id: string;
}
export interface ReconPage { items: ReconException[]; total: number; page: number; page_size: number }
export const fetchExceptions = (page = 1, statusFilter?: string) => {
  const qs = new URLSearchParams({ page: String(page), page_size: "50" });
  if (statusFilter) qs.set("status", statusFilter);
  return api.get<ReconPage>(`/api/v1/reconciliation/exceptions?${qs}`);
};
export const resolveException = (id: string, s: string, resolution?: string) =>
  api.patch<{ ok: boolean; status: string }>(`/api/v1/reconciliation/exceptions/${id}`,
    { status: s, resolution });
export const runReconciliation = (body: { period_start: string; period_end: string }) =>
  api.post<{ run_id: string; exceptions_open: number; material_open: number; summary: Record<string, string> }>(
    "/api/v1/reconciliation/runs", { ...body, tolerance_abs: "0.000001" });

export interface MarginCustomer {
  customer_id: string; customer: string; code: string; target_margin_pct: number | null;
  provider_cost: string; revenue: string; margin: string; margin_pct: string | null;
  unbilled_usage: boolean; invoiced: boolean;
}
export interface MarginSummary {
  period_start: string | null;
  totals: { revenue: string; provider_cost: string; margin: string; margin_pct: string | null;
            low_margin_customers: number; negative_margin_customers: number; unbilled_customers: number };
  customers: MarginCustomer[];
}
export const fetchMargins = (periodStart?: string) =>
  api.get<MarginSummary>(`/api/v1/margins/summary${periodStart ? `?period_start=${periodStart}` : ""}`);

export interface DqSummary {
  billing_periods_present: string[]; missing_parsed_files_periods: string[];
  failed_jobs: { file_id: string; filename: string; status: string; error: string }[];
  unmapped_usage: { account: string; amount: string }[];
  quarantined_by_reason: Record<string, number>;
}
export const fetchDq = () => api.get<DqSummary>("/api/v1/data-quality");

export const loadSynthetic = (months: string) =>
  api.postForm<{ results: { month: string; canonical: number; duplicates: number; quarantined: number;
    unmapped_accounts: string[]; skipped_duplicate_file: boolean; file_id: string; status: string }[] }>(
    "/api/v1/ingestion/synthetic/load", new URLSearchParams({ months }));

export interface UsageGroup { group: string; provider_cost: string; list_cost: string; credit: string; rows: number }
export const fetchUsage = (groupBy: string, periodStart?: string, page = 1) => {
  const qs = new URLSearchParams({ group_by: groupBy, page: String(page), page_size: "20" });
  if (periodStart) qs.set("period_start", periodStart);
  return api.get<{ group_by: string; items: UsageGroup[]; total: number; page: number; page_size: number }>(
    `/api/v1/usage/explorer?${qs}`);
};

export const createContract = (body: Record<string, unknown>) =>
  api.post<{ id: string; version_id: string }>("/api/v1/contracts", body);
export const createRule = (body: Record<string, unknown>) =>
  api.post<{ id: string; version_id: string }>("/api/v1/billing-rules", body);
export const createRuleVersion = (ruleId: string, body: Record<string, unknown>) =>
  api.post<{ id: string; version_number: number }>(`/api/v1/billing-rules/${ruleId}/versions`, body);
export const publishRuleVersion = (versionId: string) =>
  api.post<{ ok: boolean; status: string }>(`/api/v1/billing-rule-versions/${versionId}/publish`);
export const setBindings = (versionId: string, rule_bindings: unknown[]) =>
  api.patch<{ ok: boolean }>(`/api/v1/contract-versions/${versionId}/bindings`, { rule_bindings });
export const activateContract = (versionId: string) =>
  api.post<{ ok: boolean; status: string }>(`/api/v1/contract-versions/${versionId}/activate`);

export function billingDocUrl(id: string, fmt: "pdf" | "csv"): string {
  return `/api-backend/api/v1/invoices/${id}/download?fmt=${fmt}`;
}
