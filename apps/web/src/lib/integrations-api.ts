/** AI assistant + integrations API client (Phase 5). */

import { api } from "./api";

/* ---------- assistant ---------- */

export interface AssistantCitation {
  type: string;
  id?: string;
  ref?: string;
  scope?: string;
  [k: string]: unknown;
}

export interface AssistantAnswer {
  intent: string;
  text: string;
  facts: Record<string, unknown>[];
  estimates: Record<string, unknown>[];
  citations: AssistantCitation[];
  mode: string;
  refused: boolean;
  refusal_reason: string | null;
  sensitive: boolean;
  tools_used: string[];
  latency_ms: number;
}

export const askAssistant = (question: string) =>
  api.post<AssistantAnswer>("/api/v1/assistant/ask", { question });

export interface AssistantCapabilities {
  mode: string;
  model_backed: boolean;
  read_only: boolean;
  can_write: boolean;
  intents: { id: string; example: string; requires: string[]; partner_only?: boolean }[];
  guarantees: string[];
}

export const assistantCapabilities = () =>
  api.get<AssistantCapabilities>("/api/v1/assistant/capabilities");

export interface AiAuditRow {
  id: string; asked_by_email: string | null; question: string; intent: string | null;
  mode: string; refused: boolean; refusal_reason: string | null; sensitive: boolean;
  tools_used: string[]; citations: AssistantCitation[]; answer_summary: string;
  latency_ms: number | null; created_at: string;
}
export const fetchAssistantAudit = () =>
  api.get<{ items: AiAuditRow[]; total: number }>("/api/v1/assistant/audit?page_size=25");

/* ---------- integrations ---------- */

export interface WebhookEndpointRow {
  id: string; url: string; events: string[]; status: string;
  description: string | null; secret_configured: boolean;
}
export interface IntegrationRow {
  id: string; kind: string; name: string; status: string;
  config: Record<string, unknown>; connected: boolean; last_check_at: string | null;
}
export interface Overview {
  event_types: string[];
  webhook_endpoints: WebhookEndpointRow[];
  integrations: IntegrationRow[];
  pending_deliveries: number;
}

export const fetchOverview = () => api.get<Overview>("/api/v1/integrations/overview");
export const createWebhook = (body: { url: string; events: string[]; description?: string }) =>
  api.post<{ id: string; signing_secret: string; note: string }>("/api/v1/integrations/webhooks", body);
export const testWebhook = (id: string) =>
  api.post<{ queued: number; attempts_this_call: number;
    latest: { status: string; attempts: number; response_code: number | null;
      error: string | null }[] }>(`/api/v1/integrations/webhooks/${id}/test`, {});
export interface DeliveryRow {
  id: string; event: string; status: string; attempts: number;
  response_code: number | null; created_at: string | null;
  delivered_at: string | null; error: string | null;
}
export const fetchDeliveries = (id: string) =>
  api.get<{ items: DeliveryRow[]; total: number }>(
    `/api/v1/integrations/webhooks/${id}/deliveries?page_size=25`);
export const createIntegration = (body: { kind: string; name: string; config?: Record<string, unknown> }) =>
  api.post<{ id: string; connected: boolean; note: string }>("/api/v1/integrations", body);
export const checkIntegration = (id: string) =>
  api.post<{ connected: boolean; detail: string }>(`/api/v1/integrations/${id}/check`, {});

/** ERP journal export — direct anchor download (GET, cookie auth). */
export function erpExportUrl(id: string, fmt: "json" | "csv"): string {
  return `/api-backend/api/v1/invoices/${id}/export?fmt=${fmt}`;
}
