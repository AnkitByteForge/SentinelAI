# ── Builder: full deps (incl. dev, needed by tailwind/typescript at build time) ──
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json .
RUN npm install
COPY . .
RUN npm run build

# ── Runtime: production deps only, non-root user ────────────────────────
# node:alpine already ships a non-root "node" user at uid/gid 1000 —
# reused here rather than creating a new one, which collides on that gid.
FROM node:20-alpine
WORKDIR /app

COPY package*.json .
RUN npm install --omit=dev

COPY --from=builder --chown=node:node /app/.next ./.next
COPY --from=builder --chown=node:node /app/public ./public
COPY --from=builder --chown=node:node /app/next.config.js ./next.config.js

USER node

CMD ["npm", "start"]
