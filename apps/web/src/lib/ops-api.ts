/** Phase 2 API hooks: approvals, credits, commitments, reports, branding. */

import { api } from "@/lib/api";

// ------------------------------------------------------------------ approvals

export interface ApprovalRow {
  id: string; request_number: string; kind: string; entity_type: string;
  entity_id: string; summary: string; impact_amount: string | null;
  currency: string; status: string; maker: string; maker_email: string;
  created_at: string; decided_at: string | null; decision_note: string | null;
}
export const fetchApprovals = (page = 1, statusFilter?: string) => {
  const qs = new URLSearchParams({ page: String(page), page_size: "50" });
  if (statusFilter) qs.set("status", statusFilter);
  return api.get<{ items: ApprovalRow[]; total: number; page: number; page_size: number }>(
    `/api/v1/approvals?${qs}`);
};
export const decideApproval = (id: string, decision: "approved" | "denied" | "cancelled", note: string) =>
  api.post<{ ok: boolean; status: string }>(
    `/api/v1/approvals/${id}/decide?decision=${decision}`, { note });

// -------------------------------------------------------------------- credits

export interface CreditRow {
  id: string; display_name: string; kind: string; provider: string;
  customer: string | null; amount_total: string; amount_used: string;
  remaining: string; currency: string; allocation_status: string;
  expires_at: string | null; notes: string | null;
}
export const fetchCredits = (statusFilter?: string) =>
  api.get<CreditRow[]>(`/api/v1/credits${statusFilter ? `?allocation_status=${statusFilter}` : ""}`);
export const createCredit = (body: Record<string, unknown>) =>
  api.post<{ id: string; allocation_status: string }>("/api/v1/credits", body);
export const allocateCredit = (id: string, customer_id: string, amount: string) =>
  api.post<{ ok: boolean; allocation_status: string; remaining: string }>(
    `/api/v1/credits/${id}/allocate`, { customer_id, amount });

// ---------------------------------------------------------------- commitments

export interface CommitmentRow {
  id: string; kind: string; display_name: string; external_id: string | null;
  provider: string; customer: string | null; start_date: string; end_date: string | null;
  hourly_commitment: string | null; currency: string; status: string;
  coverage_scope: Record<string, unknown>;
}
export const fetchCommitments = () => api.get<CommitmentRow[]>("/api/v1/commitments");
export const createCommitment = (body: Record<string, unknown>) =>
  api.post<{ id: string }>("/api/v1/commitments", body);
export interface Coverage {
  period_start: string; period_end: string; ondemand_equivalent: string;
  provider_billed: string; covered_ondemand_equivalent: string;
  coverage_pct: string | null; commitment_savings: string;
}
export const fetchCoverage = (periodStart: string, periodEnd: string) =>
  api.get<Coverage>(`/api/v1/commitments/coverage?period_start=${periodStart}&period_end=${periodEnd}`);

// -------------------------------------------------------------------- reports

export interface ReportDef { key: string; description: string }
export const fetchReportCatalog = () => api.get<ReportDef[]>("/api/v1/reports/catalog");

export interface ScheduleRow {
  id: string; name: string; report_key: string; cadence: string;
  day_of_month: number; day_of_week: number; hour_utc: number;
  recipients: string[]; period_offset_months: number; enabled: boolean;
  last_run_at: string | null; next_run_at: string | null;
}
export const fetchSchedules = () => api.get<ScheduleRow[]>("/api/v1/reports/schedules");
export const createSchedule = (body: Record<string, unknown>) =>
  api.post<{ id: string; next_run_at: string }>("/api/v1/reports/schedules", body);
export const toggleSchedule = (id: string) =>
  api.post<{ ok: boolean; enabled: boolean }>(`/api/v1/reports/schedules/${id}/toggle`, {});
export const reportDownloadUrl = (key: string, periodStart?: string) =>
  `/api-backend/api/v1/reports/${key}/run?fmt=csv` +
  (periodStart ? `&period_start=${encodeURIComponent(periodStart)}` : "");

// -------------------------------------------------------------------- ops

export const fetchPeriodCloses = () =>
  api.get<{ id: string; period_start: string; period_end: string; status: string;
    closed_at: string | null; summary: Record<string, unknown> }[]>("/api/v1/period-closes");
export const closePeriod = (period_start: string, period_end: string) =>
  api.post<{ ok: boolean; status: string; summary: Record<string, unknown> }>(
    "/api/v1/period-closes/close", { period_start, period_end });
export const requestWaiver = (exceptionId: string, reason: string) =>
  api.post<{ id: string; request_number: string }>(
    `/api/v1/reconciliation/exceptions/${exceptionId}/waive`,
    { exception_id: exceptionId, reason });
export const markWaived = (exceptionId: string) =>
  api.post<{ ok: boolean; status: string }>(
    `/api/v1/reconciliation/exceptions/${exceptionId}/mark-waived`, {});

export interface Leakage {
  unmapped_usage: { account: string; amount: string }[];
  unbilled_usage: { customer: string; period: string; amount: string }[];
  unallocated_credits: { id: string; name: string; kind: string; remaining: string; expires_at: string | null }[];
  invoice_run_drift: { invoice: string; invoice_total: string; run_customer_subtotal: string }[];
}
export const fetchLeakage = (periodStart?: string) =>
  api.get<Leakage>(`/api/v1/revenue-leakage${periodStart ? `?period_start=${periodStart}` : ""}`);

// ---------------------------------------------------------------- billing notes

export interface NoteRow {
  id: string; note_number: string; kind: string; invoice_id: string;
  customer_id: string; amount: string; currency: string; status: string;
  reason: string; lines: { line_number: number; description: string; amount: string }[];
  created_at: string; issued_at: string | null;
}
export const fetchNotes = (invoiceId?: string) =>
  api.get<NoteRow[]>(`/api/v1/billing-notes${invoiceId ? `?invoice_id=${invoiceId}` : ""}`);
export const createNote = (body: Record<string, unknown>) =>
  api.post<{ id: string; note_number: string; amount: string; status: string }>(
    "/api/v1/billing-notes", body);
export const issueNote = (id: string) =>
  api.post<{ ok: boolean; status: string; invoice_status: string }>(
    `/api/v1/billing-notes/${id}/issue`, {});
export const noteDownloadUrl = (id: string, fmt: "pdf" | "csv") =>
  `/api-backend/api/v1/billing-notes/${id}/download?fmt=${fmt}`;

// ------------------------------------------------------------------- branding

export interface BrandingFull {
  product_name: string; logo_url: string | null; primary_color: string; accent_color: string;
  support_email: string | null; email_sender_name: string | null; custom_domain: string | null;
  terminology: Record<string, string>; feature_flags: Record<string, boolean>;
  org_path: string;
}
export const fetchBrandingCurrent = () => api.get<BrandingFull>("/api/v1/branding/current");
export const updateBranding = (body: Record<string, unknown>) =>
  api.put<BrandingFull>("/api/v1/branding", body);
