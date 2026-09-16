/** App-wide state: current user + capabilities + branding, loaded once. */

"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Branding } from "@cloudpartnerops/shared";

export interface NavEntry {
  key: string;
  label: string;
  href: string;
  permission: string;
  available_from_phase: number;
  granted: boolean;
}

export interface Capabilities {
  product_version: string;
  roles: string[];
  permissions: string[];
  navigation: NavEntry[];
}

export interface Me {
  id: string;
  email: string;
  display_name: string;
  org_id: string;
  org_kind: string;
  roles: string[];
  permissions: string[];
  scope_org_prefixes: string[];
  mfa_enabled: boolean;
}

interface AppState {
  me: Me | null;
  capabilities: Capabilities | null;
  branding: Branding | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AppState>({
  me: null,
  capabilities: null,
  branding: null,
  loading: true,
  refresh: async () => {},
  logout: async () => {},
});

export function useApp(): AppState {
  return useContext(Ctx);
}

export const BrandingCtx = createContext<Branding | null>(null);

export function useBranding(): Branding | null {
  return useContext(BrandingCtx);
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [capabilities, setCaps] = useState<Capabilities | null>(null);
  const [branding, setBranding] = useState<Branding | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const u = await api.get<Me>("/api/v1/auth/me");
      setMe(u);
      const caps = await api.get<Capabilities>("/api/v1/capabilities");
      setCaps(caps);
      try {
        const b = await api.get<Branding>("/api/v1/branding/current");
        setBranding(b);
        applyBranding(b);
      } catch {
        /* public fallback handled by caller pages */
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setMe(null);
        setCaps(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/v1/auth/logout");
    } finally {
      setMe(null);
      setCaps(null);
      window.location.href = "/login";
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Ctx.Provider value={{ me, capabilities, branding, loading, refresh, logout }}>
      <BrandingCtx.Provider value={branding}>{children}</BrandingCtx.Provider>
    </Ctx.Provider>
  );
}

export function applyBranding(b: Partial<Pick<Branding, "primary_color" | "accent_color" | "product_name" | "terminology">> | null) {
  if (typeof document === "undefined" || !b) return;
  if (b.primary_color) document.documentElement.style.setProperty("--brand-primary", b.primary_color);
  if (b.accent_color) document.documentElement.style.setProperty("--brand-accent", b.accent_color);
  const name = b.terminology?.product_name ?? b.product_name;
  if (name) document.title = name;
}
