# 0007 — Branding is data, not code

Date: 2026-09-16 · Status: Accepted

## Context
The product must be rebrandable per distributor/MSP: name, logo, colors,
terminology, invoice/report branding, email identity, feature visibility.

## Decision
`branding_configs` rows attach to an organization path; resolution walks the
caller's path upward — nearest ancestor wins, platform root is the default
(UI never hardcodes the brand). Colors reach CSS through custom properties
injected from `GET /branding/current` (`--brand-primary`, `--brand-accent`),
consumed via Tailwind `@theme` tokens. Logos upload through validated
PNG/JPEG-only, size-capped, content-sniffed storage writes; the DB keeps
object keys only. Invoices snapshot branding at issue time so historical
documents never change when a tenant rebrands. Terminology is a JSON map
(`{"invoice": "Statement", …}`) the UI reads for labels.

## Consequences
- Rebrand = data edit (or API call), zero deploys.
- UI must avoid literal brand strings (code-review checklist item).
- Two branding reads exist (public root for the login screen, scoped for
  signed-in users) — intentional separation of anonymous vs tenant context.
