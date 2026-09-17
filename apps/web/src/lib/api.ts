/** Typed API client for the Cloud PartnerOps backend.
 *
 * Browser traffic uses the same-origin Next rewrite (/api-backend → FastAPI)
 * so session cookies and CSRF stay first-party. Money arrives as Decimal
 * strings — never re-parsed into floats for arithmetic (see shared/money).
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message?: string,
  ) {
    super(message ?? code);
  }
}

const BASE = "/api-backend";

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[2]) : null;
}

export function csrfToken(): string | null {
  return readCookie("cpo_csrf");
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, headers, ...rest } = init;
  const isUnsafe = rest.method && !["GET", "HEAD"].includes(rest.method.toUpperCase());
  const h = new Headers(headers);
  if (json !== undefined) h.set("content-type", "application/json");
  if (isUnsafe) {
    const csrf = csrfToken();
    if (csrf) h.set("X-CSRF-Token", csrf);
  }
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    ...rest,
    headers: h,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON */
  }
  if (!res.ok) {
    const detail = (data as { detail?: { code?: string } | string })?.detail;
    const code = typeof detail === "object" && detail?.code ? detail.code : `http_${res.status}`;
    throw new ApiError(res.status, code);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, json?: unknown) =>
    request<T>(path, { method: "POST", json: json ?? {} }),
  postForm: <T>(path: string, form: URLSearchParams) =>
    request<T>(path, { method: "POST", body: form.toString(),
      headers: { "content-type": "application/x-www-form-urlencoded" } }),
  put: <T>(path: string, json?: unknown) => request<T>(path, { method: "PUT", json: json ?? {} }),
  patch: <T>(path: string, json?: unknown) =>
    request<T>(path, { method: "PATCH", json: json ?? {} }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
