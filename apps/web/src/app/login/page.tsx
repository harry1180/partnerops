"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useApp, applyBranding } from "@/lib/app-state";
import { Button, Card, Field, Input } from "@cloudpartnerops/ui";

interface PublicBranding {
  product_name: string;
  primary_color: string;
  accent_color: string;
  logo_present: boolean;
}

interface DemoAccount {
  email: string;
  role: string;
}

const DEMO_PASSWORD = process.env.NEXT_PUBLIC_DEMO_PASSWORD ?? "Demo-2026-LocalOnly";

export default function LoginPage() {
  const router = useRouter();
  const { me, refresh } = useApp();
  const [branding, setBranding] = useState<PublicBranding | null>(null);
  const [accounts, setAccounts] = useState<DemoAccount[] | null>(null);
  const [email, setEmail] = useState("msp@northwind-msp.example.com");
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<PublicBranding>("/api/v1/branding/public")
      .then((b) => {
        setBranding(b);
        applyBranding(b);
      })
      .catch(() => setBranding(null));
    api
      .get<{ accounts: DemoAccount[] }>("/api/v1/admin/demo-accounts")
      .then((d) => setAccounts(d.accounts))
      .catch(() => setAccounts(null));
  }, []);

  useEffect(() => {
    if (me) router.replace("/overview");
  }, [me, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/v1/auth/login", { email, password });
      await refresh();
      router.replace("/overview");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(
          err.code === "invalid_credentials"
            ? "Invalid email or password."
            : err.code === "rate_limited"
              ? "Too many attempts — wait a minute and retry."
              : `Sign-in failed (${err.code}).`,
        );
      } else {
        setError("Cannot reach the API. Is the backend running on port 8000?");
      }
    } finally {
      setBusy(false);
    }
  }

  const name = branding?.product_name ?? "Cloud PartnerOps";

  return (
    <div className="flex min-h-screen items-center justify-center bg-ink-950 px-4">
      <div className="w-full max-w-5xl overflow-hidden rounded-2xl shadow-2xl lg:grid lg:grid-cols-5">
        <div
          className="hidden lg:col-span-2 lg:flex lg:flex-col lg:justify-between lg:p-10"
          style={{ background: branding?.primary_color ?? "#0f3d5c" }}
        >
          <div>
            <div className="grid h-11 w-11 place-items-center rounded-xl bg-white/10 font-display text-lg font-bold text-white">
              {name.slice(0, 1)}
            </div>
            <h1 className="mt-6 font-display text-2xl font-semibold leading-snug text-white">{name}</h1>
            <p className="mt-3 max-w-xs text-sm leading-relaxed text-white/70">
              From raw cloud billing to auditable, contract-aware customer invoices — with margin you
              can explain line by line.
            </p>
          </div>
          <p className="text-[11px] leading-relaxed text-white/50">
            Multi-tenant PartnerOps · Cloud financial management · FinOps & governance
          </p>
        </div>

        <Card className="lg:col-span-3 rounded-none border-0 p-8 sm:p-10">
          <h2 className="font-display text-lg font-semibold">Sign in</h2>
          <p className="mt-1 text-sm text-ink-500">
            {name} is in local demo mode. Choose a seeded account to explore each role.
          </p>

          <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
            <Field label="Work email" required>
              {(id, desc) => (
                <Input
                  id={id}
                  aria-describedby={desc}
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="font-mono text-xs"
                  required
                />
              )}
            </Field>
            <Field label="Password" required hint="Demo password for every seeded account.">
              {(id) => (
                <Input
                  id={id}
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              )}
            </Field>

            {error && (
              <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">
                {error}
              </p>
            )}

            <Button type="submit" loading={busy} className="w-full">
              {busy ? "Signing in" : "Sign in"}
            </Button>
          </form>

          {accounts && accounts.length > 0 && (
            <div className="mt-6 border-t border-ink-100 pt-4">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">
                Seeded demo accounts
              </p>
              <ul className="mt-2 grid gap-1 sm:grid-cols-2" aria-label="Demo accounts">
                {accounts.map((a) => (
                  <li key={`${a.email}-${a.role}`}>
                    <button
                      type="button"
                      onClick={() => {
                        setEmail(a.email);
                        setError(null);
                      }}
                      className={`cpo-focus w-full rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
                        email === a.email
                          ? "border-[var(--brand-primary)] bg-[var(--brand-primary)]/5"
                          : "border-ink-200 hover:bg-ink-50"
                      }`}
                    >
                      <span className="block truncate font-medium text-ink-900">{a.role.replace(/_/g, " ")}</span>
                      <span className="block truncate font-mono text-[10px] text-ink-400">{a.email}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
