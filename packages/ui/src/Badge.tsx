import { cn } from "./cn";

export type Tone = "neutral" | "positive" | "negative" | "warning" | "info" | "brand";

const TONES: Record<Tone, string> = {
  neutral: "bg-ink-100 text-ink-800",
  positive: "bg-emerald-50 text-positive ring-emerald-200",
  negative: "bg-red-50 text-negative ring-red-200",
  warning: "bg-amber-50 text-warning ring-amber-200",
  info: "bg-sky-50 text-sky-800 ring-sky-200",
  brand: "bg-[var(--brand-primary)]/10 text-[var(--brand-primary)] ring-[var(--brand-primary)]/20",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Domain status pills — the single mapping of lifecycle state → tone. */
const STATUS_TONES: Record<string, Tone> = {
  draft: "neutral",
  calculated: "info",
  under_review: "warning",
  pending_approval: "warning",
  pending: "warning",
  approved: "positive",
  published: "positive",
  active: "positive",
  issued: "brand",
  exported: "brand",
  paid_or_settled: "positive",
  disputed: "negative",
  corrected: "info",
  voided: "negative",
  rejected: "negative",
  failed: "negative",
  open: "warning",
  running: "info",
  completed: "positive",
  queued: "neutral",
  superseded: "neutral",
  expired: "neutral",
  waived: "info",
  resolved: "positive",
  investigating: "warning",
  unallocated: "warning",
  allocated: "positive",
  unmapped: "warning",
  mapped: "positive",
  quarantined: "negative",
  parsed: "positive",
  duplicate: "warning",
  suspended: "negative",
  archived: "neutral",
  closed: "positive",
};

export function StatusPill({ status, className }: { status: string; className?: string }) {
  const tone = STATUS_TONES[status] ?? "neutral";
  return (
    <Badge tone={tone} className={cn("capitalize", className)}>
      {status.replace(/_/g, " ")}
    </Badge>
  );
}
