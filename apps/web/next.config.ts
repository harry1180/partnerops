import type { NextConfig } from "next";

const apiBase = process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // `next build` (NODE_ENV=production) writes to its own dir so a running
  // `next dev` server is never clobbered (see ADR-0013); dev uses .next.
  // `next start` also runs with NODE_ENV=production and finds the build dir.
  distDir: process.env.NODE_ENV === "production" ? ".next-build" : ".next",
  // Standalone tracing uses symlinks that Windows without developer-mode
  // cannot create; the production image (Linux) sets NEXT_OUTPUT=standalone.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  async headers() {
    // Security headers for production builds (staging/prod `next start` and
    // the docker image). Dev is excluded deliberately: Next dev injects
    // inline/HMR scripts that a strict CSP would block (breaks local dev +
    // the Playwright journey). Headers are verified against a prod build
    // below (see docs/reports/phase-6-report.md).
    if (process.env.NODE_ENV === "development") return [];
    // CSP: no inline or remote scripts; style attributes are used for
    // branding colors, so style-src allows 'unsafe-inline' (documented).
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
          {
            key: "Content-Security-Policy",
            value: "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
              + "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
              + "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
          },
        ],
      },
    ];
  },
  async rewrites() {
    // Same-origin API proxy for the browser (cookies + CSRF stay simple).
    // NEXT_PUBLIC_API_URL still points at this app's /api-backend prefix.
    return [
      {
        source: "/api-backend/:path*",
        destination: `${apiBase}/:path*`,
      },
    ];
  },
};

export default nextConfig;
