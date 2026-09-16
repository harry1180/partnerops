"use client";

import { formatMoney } from "@cloudpartnerops/shared";
import { cn } from "./cn";

export function MetricCard({
  label,
  value,
  delta,
  deltaTone = "neutral",
  hint,
  className,
}: {
  label: string;
  value: string;
  delta?: string;
  deltaTone?: "positive" | "negative" | "neutral";
  hint?: string;
  className?: string;
}) {
  return (
    <div className={cn("cpo-card px-5 py-4", className)}>
      <div className="text-xs font-medium uppercase tracking-wide text-ink-500">{label}</div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="font-display text-2xl font-semibold text-ink-950 tabular-nums">{value}</span>
        {delta && (
          <span
            className={cn(
              "text-xs font-medium tabular-nums",
              deltaTone === "positive" && "text-positive",
              deltaTone === "negative" && "text-negative",
              deltaTone === "neutral" && "text-ink-500",
            )}
          >
            {delta}
          </span>
        )}
      </div>
      {hint && <div className="mt-1 text-xs text-ink-400">{hint}</div>}
    </div>
  );
}

/** Renders a Decimal-string money amount. Display only — all money math for
 * invoicing happens server-side on NUMERIC. */
export function MoneyCell({
  value,
  currency = "USD",
  className,
}: {
  value: string | null | undefined;
  currency?: string;
  className?: string;
}) {
  if (value === null || value === undefined) return <span className="text-ink-400">—</span>;
  const neg = value.trim().startsWith("-");
  return (
    <span className={cn("tabular-nums", neg && "text-negative", className)}>{formatMoney(value, currency)}</span>
  );
}
