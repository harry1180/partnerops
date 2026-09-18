"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useApp, useBranding } from "@/lib/app-state";
import { Spinner } from "@cloudpartnerops/ui";

const GROUPS: Array<{ label: string | null; keys: string[] }> = [
  { label: null, keys: ["overview", "portal_overview"] },
  { label: "Portal", keys: ["portal_usage", "portal_invoices", "portal_assistant"] },
  { label: "Billing", keys: ["customers", "cloud_accounts", "contracts", "billing_rules", "commitments", "credits", "pricing", "usage", "invoices", "reconciliation", "margins"] },
  { label: "Controls", keys: ["approvals", "reports"] },
  { label: "FinOps", keys: ["budgets", "optimization", "governance"] },
  { label: "Operations", keys: ["assistant", "integrations", "audit", "administration"] },
];

export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const { me, capabilities, loading, logout } = useApp();
  const branding = useBranding();
  const pathname = usePathname();
  const router = useRouter();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading workspace" />
      </div>
    );
  }
  if (!me || !capabilities) {
    router.replace("/login");
    return null;
  }

  const navByKey = new Map(capabilities.navigation.map((n) => [n.key, n]));
  const productName = branding?.product_name ?? "Cloud PartnerOps";

  return (
    <div className="flex min-h-screen">
      <nav
        aria-label="Main navigation"
        className="cpo-chrome fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-ink-200 bg-ink-950 text-ink-200"
      >
        <div className="flex items-center gap-2.5 border-b border-ink-800 px-4 py-4">
          <span
            aria-hidden
            className="grid h-8 w-8 place-items-center rounded-lg text-sm font-bold text-white"
            style={{ background: branding?.primary_color ?? "var(--brand-primary)" }}
          >
            {productName.slice(0, 1)}
          </span>
          <span className="truncate font-display text-sm font-semibold text-white">{productName}</span>
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-3">
          {GROUPS.map((group, gi) => {
            const items = group.keys
              .map((k) => navByKey.get(k))
              .filter((n): n is NonNullable<typeof n> => Boolean(n));
            if (items.length === 0) return null;
            return (
              <div key={gi} className="mb-4">
                {group.label && (
                  <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-widest text-ink-500">
                    {group.label}
                  </div>
                )}
                <ul className="space-y-0.5">
                  {items.map((item) => {
                    const active = pathname === item.href || pathname.startsWith(item.href + "/");
                    const future = !item.granted || item.available_from_phase > (capabilities.current_phase ?? 0);
                    return (
                      <li key={item.key}>
                        {future ? (
                          <span
                            title={item.granted
                              ? "Not available in this release yet"
                              : `Requires permission ${item.permission}`}
                            aria-disabled
                            className="flex cursor-not-allowed items-center gap-2 rounded-md px-2 py-1.5 text-xs text-ink-500/60"
                          >
                            {item.label}
                            <span className="ml-auto rounded bg-ink-800 px-1.5 py-0.5 text-[9px] font-medium uppercase text-ink-400">
                              {item.granted ? "Coming later" : `Phase ${item.available_from_phase}`}
                            </span>
                          </span>
                        ) : (
                          <Link
                            href={item.href}
                            aria-current={active ? "page" : undefined}
                            className={`cpo-focus flex items-center rounded-md px-2 py-1.5 text-xs font-medium ${
                              active ? "bg-ink-800 text-white" : "text-ink-300 hover:bg-ink-900 hover:text-white"
                            }`}
                          >
                            {item.label}
                          </Link>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
        <div className="border-t border-ink-800 px-3 py-3 text-[11px]">
          <div className="truncate font-medium text-white">{me.display_name}</div>
          <div className="truncate text-ink-400">{me.email}</div>
          <div className="mt-0.5 truncate uppercase tracking-wide text-ink-500">{me.roles.join(", ")}</div>
          <button
            type="button"
            onClick={() => void logout()}
            className="cpo-focus mt-2 rounded px-1 py-0.5 text-ink-400 underline-offset-2 hover:text-white hover:underline"
          >
            Sign out
          </button>
        </div>
      </nav>
      <main id="main" className="ml-60 flex-1 px-6 py-6 lg:px-10">
        {children}
      </main>
    </div>
  );
}
