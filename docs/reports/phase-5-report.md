# Phase 5 report — AI assistant & integrations

Date: 2026-09-18 · follows Phase 4 (FinOps & governance)

## Completed functionality

| Area | What shipped |
|------|--------------|
| Assistant | Deterministic retrieval (ADR-0018): 10 read-only tools behind an intent router — invoice change drivers, rules-on-invoice, lineage, margin-below-target, unbilled accounts, unallocated credits, recon difference, top verified savings, anomaly summary, customer-safe draft. Facts/estimates separated; every answer cites entity ids; unmatched questions are refused with a reason. |
| Assistant audit | Every query (answered or refused) → `AIQueryAudit` (intent, mode, tools, citations, sensitive flag, latency); sensitive queries additionally → `audit_events`. `/assistant/audit` scoped view for auditors. |
| Assistant API/UI | `POST /assistant/ask`, `GET /assistant/capabilities` (honest self-description incl. `model_backed:false`), console page `/assistant` (+ AI audit table) and portal page `/portal/assistant` (no audit, own-scope answers only). |
| Webhooks | `webhook_endpoints`/`webhook_deliveries` (Phase 0 tables) wired: create endpoint (signing secret shown exactly once, masked everywhere else), test-send, deliveries feed, manual sweep. HMAC-SHA256 over `timestamp.body`, `X-CPPartnerOps-Signature` + `X-CPPartnerOps-Timestamp` + `Idempotency-Key`. Retry-capped sweep (3 attempts) via Celery beat every 5 min. SSRF validation: http/https only, metadata/link-local always refused, private ranges only in local/test. Events emitted for real: `invoice.issued` (lifecycle), `dispute.created`, `report.ready`; ancestor-chain endpoint matching with explicit RLS scope per caller. |
| Email transport | Queued `notification_outbox` emails flush through a working local transport to `logs/notifications.ndjson` (labeled, marked sent); SMTP adapters plug in at the same seam. |
| Integrations registry | Kind + name + config rows; `connected` reflects an actual transport check only (slack/teams URL validation); everything else honestly reports "not connected" — no fake pings. |
| ERP export | `GET /invoices/{id}/export?fmt=json|csv` — `cpo.erp.v1` journal doc: header, customer, PO ref, all totals, customer-visible lines only. Provider cost, margin, internal notes structurally excluded. Audited (`export.generated`) + `ExportJob` row. Buttons on the invoice page. |
| API tokens | Existing Phase 0 machinery verified end-to-end for this phase: create (shown once, hash stored), Bearer auth, scopes intersect user permissions, rotation. UI pointer on the Integrations page. |

## Bugs found and fixed while building

- `ApiToken.expires_at` compared naive-vs-aware on SQLite in the auth path
  (bearer requests would 500 once a token expired) — normalized in `deps.py`.
- `queue_event` matched endpoints at exact org only — an MSP configuring a
  webhook at its root would never receive customer-subtree events. Now
  ancestor-chain match with caller-supplied RLS scope; API callers pass
  their own root, trusted workers pass "/".
- Test-send swept globally: one endpoint's dead target blocked others'
  deliveries behind 8s timeouts (39s response observed live). Sweep is now
  per-endpoint on test-send; the beat sweep remains global.
- Phase 4 live smoke's audit check used a page-window query that repeated
  smoke runs pushed out of the window — switched to per-action filters.
- `test_scheduled_reports` counted all `ExportJob` rows globally; ERP export
  legitimately adds more — scoped the assertion to scheduled jobs.

## Verification (all green)

| Gate | Result |
|------|--------|
| ruff (app+tests) / mypy (96 files) | clean |
| pytest | **125 passed** (10 new Phase 5 tests) |
| RLS integration (live PG 16) | passed — Phase 0 tables (webhooks, outbox, ai_query_audit, integrations) already in the tenant_scope policy list |
| smoke_phase5_live.py | **SMOKE5_OK — 15 steps**, incl. a real HTTP listener verifying receiver-side HMAC for integration.test, invoice.issued and dispute.created events |
| smokes 1–4 | re-passed after Phase 5 changes (25 / 17 / 22 / 24 steps) |
| Playwright journey | **18 passed, 0 skipped** — step 12 is now real UI (assistant answer+refusal+audit), 12b ERP export, 12c webhook create/test/deliveries. Ran 2× consecutively |
| typecheck / vitest / next build | clean / 8 tests / compiles, /assistant /integrations /portal/assistant prerender |

## Honest limits

- The assistant is deterministic retrieval — no LLM, labeled `deterministic_demo`; a model-backed router can replace the regex table later without touching tools, scope, or audit (ADR-0018).
- Webhook secrets live in the endpoint's `secret_ref` in local mode (`local:v1:…`); production swaps to a KMS reference at the same seam (`_secret_of`).
- Hostname SSRF targets are validated at parse time only (no DNS pinning); IP literals are fully checked. Recorded as a deployment-hardening item.
- Email "sending" writes the local outbox file — a real transport with credentials is a deployment adapter, not demo fakery.
- Event catalog is 6 types; period-close/finding-open events are cheap adds at `queue_event`.

## Deferred (Phase 6+)

OIDC SSO, KMS-backed secrets, DNS-pinned egress, marketplace invoice-sync
parsing (AWS MP / Azure MCA), daily-grain anomalies, LLM-backed rephrase of
assistant answers behind the same tool boundary.
