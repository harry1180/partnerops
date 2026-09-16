"use client";

import type { ReactNode } from "react";
import { Badge } from "./Badge";
import { Button } from "./Button";

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-ink-200 px-6 py-10 text-center">
      <p className="text-sm font-medium text-ink-700">{title}</p>
      {hint && <p className="max-w-md text-xs text-ink-400">{hint}</p>}
      {action}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  detail,
  onRetry,
}: {
  title?: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-2 rounded-xl border border-red-200 bg-red-50/50 px-6 py-8 text-center">
      <Badge tone="negative">Error</Badge>
      <p className="text-sm font-medium text-ink-900">{title}</p>
      {detail && <p className="max-w-md break-words text-xs text-ink-500">{detail}</p>}
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 text-xs text-ink-400">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-200 border-t-brand-primary" aria-hidden />
      {label}…
    </div>
  );
}

/** Honest placeholder for features not yet implemented. Never rendered as an
 * actionable control — a static, clearly-labeled notice. */
export function ComingLater({ feature, phase }: { feature: string; phase: number }) {
  return (
    <div className="rounded-lg border border-dashed border-ink-300 bg-ink-50 px-4 py-3 text-xs text-ink-500">
      <span className="font-semibold text-ink-700">{feature}</span> is coming later (Phase {phase}).
      This screen does not yet offer it, and nothing here pretends to work.
    </div>
  );
}
