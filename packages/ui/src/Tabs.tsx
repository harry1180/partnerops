"use client";

import { useEffect, useState } from "react";
import { cn } from "./cn";

export interface TabItem {
  key: string;
  label: string;
  panel: React.ReactNode;
}

export function Tabs({ items, className }: { items: TabItem[]; className?: string }) {
  const [active, setActive] = useState(items[0]?.key);
  useEffect(() => {
    if (!items.some((t) => t.key === active)) setActive(items[0]?.key);
  }, [items, active]);
  const current = items.find((t) => t.key === active) ?? items[0];
  return (
    <div className={className}>
      <div role="tablist" aria-label="Views" className="flex gap-1 border-b border-ink-200">
        {items.map((t) => (
          <button
            key={t.key}
            role="tab"
            type="button"
            aria-selected={t.key === active}
            onClick={() => setActive(t.key)}
            className={cn(
              "cpo-focus -mb-px border-b-2 px-3 py-2 text-sm font-medium",
              t.key === active
                ? "border-[var(--brand-primary)] text-ink-950"
                : "border-transparent text-ink-500 hover:text-ink-800",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="pt-4">
        {current?.panel}
      </div>
    </div>
  );
}
