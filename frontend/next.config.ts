import type { NextConfig } from "next";

// The FastAPI backend has no CORS middleware, so the browser talks to it through this same-origin
// proxy: fetch("/api/be/chat") -> ${BACKEND_URL}/chat. Set BACKEND_URL in .env.local (default below).
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Lets a second dev server (e.g. pointed at a different backend) run without clobbering .next.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  cacheComponents: true,
  partialPrefetching: true,
  async rewrites() {
    return [{ source: "/api/be/:path*", destination: `${BACKEND_URL}/:path*` }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
