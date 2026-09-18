"use client";

import { api } from "@/lib/api";

/* ---------- Phase 4: FinOps + governance clients ---------- */

export interface BudgetRow {
  id: string; name: string; scope_kind: string; customer_id: string | null;
  provider: string | null; currency: string; amount: string;
  period_start: string; period_end: string;
  actual: string; projected_total: string;
  pct_of_budget: string; projected_pct: string;
  alert_threshold_pct: number; over_threshold: boolean; over_budget: boolean;
  days_elapsed: number; days_total: number;
}
export const fetchBudgets = (includeClosed = false) =>
  api.get<{ items: BudgetRow[] }>(`/api/v1/budgets${includeClosed ? "?include_closed=true" : ""}`);
export const createBudget = (body: Record<string, unknown>) =>
  api.post<{ id: string }>("/api/v1/budgets", body);
export const deleteBudget = (id: string) => api.del(`/api/v1/budgets/${id}`);

export interface AnomalyRow {
  id: string; kind: string; status: string; customer_id: string | null;
  service: string | null; detected_on: string; observed: string; baseline: string;
  z_score: string; threshold_used: string; method: string;
  evidence: Record<string, unknown>; review_note: string | null;
}
export const fetchAnomalies = (status = "open") =>
  api.get<{ items: AnomalyRow[]; total: number }>(`/api/v1/finops/anomalies?status=${status}&page_size=50`);
export const runAnomalyPass = () =>
  api.postForm<{ created: number; updated: number; subjects: number }>("/api/v1/finops/anomalies/run", new URLSearchParams());
export const reviewAnomaly = (id: string, status: string, note?: string) =>
  api.patch<{ ok: boolean; status: string }>(`/api/v1/finops/anomalies/${id}`, { status, note });

export interface RecRow {
  id: string; kind: string; status: string; customer_id: string | null;
  resource_id: string | null; service: string | null; region: string | null;
  title: string; detail: string; remediation: string | null;
  estimated_monthly_saving: string | null; realized_savings: string | null;
  realized_basis: Record<string, unknown> | null;
  confidence: string; basis: Record<string, unknown>; decision_note: string | null;
}
export interface RecList {
  items: RecRow[]; total: number; page: number; page_size: number;
  open_estimated_total: string; realized_total: string;
}
export const fetchRecommendations = (status = "open") =>
  api.get<RecList>(`/api/v1/finops/recommendations?status=${status}&page_size=50`);
export const runRecommendationPass = () =>
  api.post<{ created: number; refreshed: number; resources: number; realized: number }>(
    "/api/v1/finops/recommendations/run", {});
export const decideRecommendation = (id: string, decision: "accept" | "dismiss", note?: string) =>
  api.post<{ ok: boolean; status: string }>(`/api/v1/finops/recommendations/${id}/decision`, { decision, note });

export interface ForecastResp {
  method: string; avg_monthly_growth_pct?: string;
  months: { month: string; amount: string }[];
  forecast: { month: string; amount: string }[];
}
export const fetchForecast = () => api.get<ForecastResp>("/api/v1/finops/forecast?months_ahead=3");

export interface UnitEconomics {
  dimensions: { dimension: string; items: { key: string; amount: string; rows: number }[] }[];
}
export const fetchUnitEconomics = () => api.get<UnitEconomics>("/api/v1/finops/unit-economics");
export interface TagCompliance {
  coverage: { key: string; compliant: number; total: number; pct: number }[];
  unallocated_cost: string;
}
export const fetchTagCompliance = () => api.get<TagCompliance>("/api/v1/finops/tag-compliance");

/* ---------- governance ---------- */

export interface PolicyRow {
  id: string; name: string; kind: string; severity: string; enabled: boolean;
  parameters: Record<string, unknown>; owner_label: string | null;
  remediation: string | null; open_findings: number;
  last_evaluated_at: string | null;
}
export const fetchPolicies = () => api.get<{ items: PolicyRow[] }>("/api/v1/governance/policies");
export const createPolicy = (body: Record<string, unknown>) =>
  api.post<{ id: string; kind: string }>("/api/v1/governance/policies", body);
export const evaluatePolicies = () =>
  api.post<{ new: number; open: number; remediated: number; excepted: number;
    exceptions_expired: number; by_policy: Record<string, number> }>(
    "/api/v1/governance/evaluate", {});
export interface FindingRow {
  id: string; policy_id: string; policy_name: string; subject: string;
  severity: string; status: string; customer_id: string | null;
  evidence: Record<string, unknown>; remediation: string | null;
  owner_label: string | null; first_seen: string; last_seen: string;
  has_active_exception: boolean;
}
export const fetchFindings = (status = "open") =>
  api.get<{ items: FindingRow[]; total: number }>(`/api/v1/governance/findings?status=${status}&page_size=50`);
export const acknowledgeFinding = (id: string, note?: string) =>
  api.post<{ ok: boolean; status: string }>(`/api/v1/governance/findings/${id}/acknowledge`, { note });
export const grantException = (id: string, reason: string, expires_at: string) =>
  api.post<{ id: string; expires_at: string }>(`/api/v1/governance/findings/${id}/exception`,
    { reason, expires_at });
