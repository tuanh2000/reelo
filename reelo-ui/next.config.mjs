/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a self-contained server bundle (.next/standalone) so the Docker
  // runtime stage can ship just node + the traced deps. See reelo-ui/Dockerfile.
  output: "standalone",
};

export default nextConfig;
