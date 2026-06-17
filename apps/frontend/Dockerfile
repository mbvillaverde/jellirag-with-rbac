# Frontend (Astro SSR, Node standalone adapter) — served on :3000 behind Traefik.
FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=3000
# The chat API URL is injected per-deployment so the SSR server and the browser
# both target the public origin (never a hardcoded localhost).
ENV CHAT_ENDPOINT_URL=/api/chat/stream
COPY --from=build /app/dist ./dist
COPY package.json ./
# node adapter standalone output runs the server from dist/server/entry.mjs
EXPOSE 3000
CMD ["node", "dist/server/entry.mjs"]
