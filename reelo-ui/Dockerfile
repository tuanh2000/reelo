# syntax=docker/dockerfile:1
# =============================================================================
# reelo-ui — Next.js standalone production image (multi-stage).
#
# NEXT_PUBLIC_API_BASE is a build-time arg: Next.js inlines NEXT_PUBLIC_* into
# the client bundle at BUILD time, so the prod API URL must be baked here (it
# cannot be changed at container runtime). The release workflow passes
# --build-arg NEXT_PUBLIC_API_BASE=https://api.cognal.xyz.
#
# Build context = reelo-ui/. Build with:
#   docker build -f reelo-ui/Dockerfile \
#     --build-arg NEXT_PUBLIC_API_BASE=https://api.cognal.xyz \
#     -t ghcr.io/tuanh2000/reelo-ui:TAG reelo-ui
# =============================================================================

# ---- deps: install node_modules (cached on lockfile) -----------------------
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- build: compile the standalone server ----------------------------------
FROM node:20-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE=https://api.cognal.xyz
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- runner: minimal runtime image -----------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Run as non-root.
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# Standalone output: server.js + minimal node_modules, plus static assets and
# the public/ dir (copied separately because standalone does not include them).
COPY --from=build /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

# server.js is emitted by the standalone build at the project root.
CMD ["node", "server.js"]
