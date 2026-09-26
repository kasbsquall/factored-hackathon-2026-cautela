import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  agentRules: false,
  devIndicators: false,
  // The production build type-checks the app only. Tests read shared fixtures outside app/ (docs/format-vectors.json)
  // that a deploy shipping just app/ does not have; `npm run typecheck` still checks them locally.
  typescript: { tsconfigPath: "tsconfig.build.json" },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
