# Build context: frontend/
# The app has Next Route Handlers, so it must run as a Node server instead of a static export.

# ── Deps ────────────────────────────────────────────────
FROM node:20-slim AS deps

WORKDIR /app

# Use the lockfile so Docker builds install the same dependency graph as local builds.
COPY package.json package-lock.json ./
RUN npm ci

# ── Build ───────────────────────────────────────────────
FROM node:20-slim AS build

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Runtime backend URLs are read by Route Handlers at container startup, not baked here.
# The client defaults to the mock API (NEXT_PUBLIC_USE_MOCK !== "false"). NEXT_PUBLIC_* is
# inlined at build time, so deployed images must bake this off here — a runtime env cannot fix it.
ENV NEXT_PUBLIC_USE_MOCK=false
RUN npm run build

# ── Runtime ─────────────────────────────────────────────
FROM node:20-slim AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0

# Next standalone output includes the minimal server and traced production dependencies.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public

USER node

EXPOSE 8080

CMD ["node", "server.js"]
