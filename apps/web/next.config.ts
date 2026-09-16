import type { NextConfig } from "next";

const apiBase = process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Standalone tracing uses symlinks that Windows without developer-mode
  // cannot create; the production image (Linux) sets NEXT_OUTPUT=standalone.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
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
