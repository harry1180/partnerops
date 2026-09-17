# ADR-0013: Tailwind v4 pipeline for Next.js + shared UI library (three silent failures)

Date: 2026-09-16
Status: Accepted

## Context

The UI appeared broken in a browser while every automated gate (typecheck,
`next build`, unit tests) stayed green. Root-causing revealed THREE stacked
silent failures in the CSS pipeline:

1. **No PostCSS config at all.** The app declared Tailwind v4 but only had
   `@tailwindcss/vite` installed — a Vite plugin Next.js never runs. With no
   `postcss.config.mjs`, `@import "tailwindcss"` was served raw: zero utility
   classes generated. (Caught by the user looking at the page, not by CI.)
2. **Shared library outside Tailwind's auto-source.** After wiring
   `@tailwindcss/postcss`, utilities used ONLY inside `packages/ui`
   (`fixed inset-0 z-50` on the Modal, etc.) were still missing — Tailwind v4
   auto-detects sources from the importing app's root, and `packages/ui` sits
   outside `apps/web`. The Modal overlay collapsed to static flow, pushing
   form buttons below the viewport; Playwright timed out waiting for clicks
   that no human could perform either.
3. **`position: fixed` fragility.** The Modal was rendered inline in the page
   tree; any transformed ancestor would re-anchor it. Portal-ing to
   `document.body` removes the class of bug entirely.

## Decision

- `apps/web/postcss.config.mjs` with `@tailwindcss/postcss` is the pipeline.
- `packages/ui/src/styles.css` declares `@source "./"` so every component's
  classes are always generated, independent of the consumer's file tree.
- `Modal` renders through `createPortal(…, document.body)` (SSR-guarded).
- E2E journeys run against the dev server with `screenshot: "only-on-failure"`
  — visual/interaction dead-ends now fail tests, not just eyeballs.

## Lessons recorded

1. CSS processors failing to run are silent in Next builds. "Styles render"
   must be an asserted behavior (Playwright actionability + computed-style
   probes), never inferred from a green build.
2. Tailwind v4 auto-source detection is relative to the app root; monorepo
   shared UI packages need an explicit `@source`.
3. Long-lived `next dev` servers must not share `.next` with `next build`
   (the build clobbers dev chunks → 404 assets; observed twice this session).
   **Permanently fixed**: `distDir` is `.next-build` when
   `NODE_ENV=production` (build + `next start` + Docker runner all use it),
   `.next` otherwise — verified by rebuilding while dev served traffic.
4. pnpm does not hoist `postcss` for ad-hoc probe scripts; resolve through
   the plugin's own tree or run probes inside the app package.
