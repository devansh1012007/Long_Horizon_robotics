import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Production: emit a self-contained server (deploy/Dockerfile runs it).
  output: "standalone",
  // Pin the workspace root so lockfile detection never mis-nests the
  // standalone output (matters when the repo sits inside another workspace).
  turbopack: {
    root: process.cwd(),
  },
};

export default nextConfig;
