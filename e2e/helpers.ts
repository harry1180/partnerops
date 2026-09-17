/** API-side helpers for the Playwright journeys (direct :8001, cookie sessions). */

import { request, type APIRequestContext } from "@playwright/test";

export const API = process.env.CPO_API_BASE ?? "http://localhost:8001";
export const WEB = process.env.CPO_WEB_BASE ?? "http://localhost:3000";
export const PASSWORD = process.env.SEED_DEMO_PASSWORD ?? "Demo-2026-LocalOnly";

export interface ApiCtx {
  ctx: APIRequestContext;
  csrf: Record<string, string>;
}

/** Log in via the API and return a context carrying the session cookie. */
export async function apiLogin(email: string, password = PASSWORD): Promise<ApiCtx> {
  const ctx = await request.newContext({ baseURL: API });
  const r = await ctx.post("/api/v1/auth/login", { data: { email, password } });
  if (!r.ok()) throw new Error(`api login ${email}: ${r.status()} ${await r.text()}`);
  const token = (await ctx.storageState()).cookies.find((c) => c.name === "cpo_csrf")?.value ?? "";
  return { ctx, csrf: { "X-CSRF-Token": token } };
}

export async function apiGet<T>(a: ApiCtx, path: string): Promise<T> {
  const r = await a.ctx.get(path);
  if (!r.ok()) throw new Error(`GET ${path}: ${r.status()} ${await r.text()}`);
  return (await r.json()) as T;
}

export async function apiPost<T>(a: ApiCtx, path: string, data: unknown): Promise<T> {
  const r = await a.ctx.post(path, { data, headers: a.csrf });
  if (!r.ok()) throw new Error(`POST ${path}: ${r.status()} ${await r.text()}`);
  return (await r.json()) as T;
}

export async function apiPatch<T>(a: ApiCtx, path: string, data: unknown): Promise<T> {
  const r = await a.ctx.patch(path, { data, headers: a.csrf });
  if (!r.ok()) throw new Error(`PATCH ${path}: ${r.status()} ${await r.text()}`);
  return (await r.json()) as T;
}

/** Sign in through the browser UI (proves the real auth path). */
export async function uiLogin(page: import("@playwright/test").Page, email: string,
                              password = PASSWORD) {
  await page.goto(`${WEB}/login`);
  await page.getByLabel(/work email/i).fill(email);
  await page.getByLabel(/password/i).fill(password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await page.waitForURL(/\/(overview|portal|customers|invoices|admin)/, { timeout: 15_000 });
}

/** Deterministic unique suffix so the journey is re-runnable against a dirty demo DB. */
export const runTag = () =>
  process.env.CPO_TAG ?? String(Date.now()).slice(-7);
