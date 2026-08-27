// No rewrites() here on purpose: /api/gateway/* is served by
// app/api/gateway/[...path]/route.ts, a real server-side proxy that
// injects the backend API key. A rewrite can't add headers, so it can't
// do that — see that file's comment for the full rationale.
/** @type {import('next').NextConfig} */
const nextConfig = {}

module.exports = nextConfig