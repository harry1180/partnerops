# ADR-0018: Deterministic retrieval assistant (no LLM in the sync path)

Date: 2026-09-18
Status: accepted
Phase: 5

## Context

The charter requires an "AI PartnerOps assistant" that answers billing
questions without inventing numbers. Two properties are non-negotiable:
(1) every fact traces to a record the caller's permissions allow them to
read, and (2) nothing is generated — a wrong-but-plausible number in a
billing tool is worse than a refusal.

## Decision

Ship the assistant as **deterministic retrieval**: a fixed intent router
(regex table, 10 intents) dispatches to read-only tools that compute
answers directly from canonical billing records under the caller's RLS
scope. No model call happens in the request path (`model_backed: false`,
`mode: deterministic_demo` — labeled in API, UI, and every audit row).

- Answers carry `facts` (computed from rows), `estimates` (clearly
  separated, only for things like potential savings), and `citations`
  (entity type + id + human ref). No freeform narrative generation.
- Anything the router doesn't match is **refused with a reason** —
  the failure mode is an honest "no read-only tool matches this
  question", never an invention.
- Every query (answered or refused) writes `AIQueryAudit`; sensitive
  intents additionally write `audit_events`. The auditor role reads the
  trail; customer-portal users cannot.
- Permission enforcement is tool-level (`margin.view` for margin
  intents, partner-only intents refused for customer orgs), on top of
  org-scope RLS. Portal answers are computed from customer-safe queries
  only — no provider cost, no partner names, no cross-customer rows.

## Consequences

- +100% of answers are reproducible from the same data; the UI can show
  a "deterministic" badge truthfully.
- Answer style is plain; natural-language paraphrase or follow-up
  reasoning would need a model. The seam is explicit: swap the router
  for an LLM that calls the same tools with the same scope (function-
  calling mode), keeping the audit and permission layer unchanged.
- Intent coverage is the 10 registered tools; "what can it do" is
  answered by `/assistant/capabilities` itself, so the UI never
  oversells it.
