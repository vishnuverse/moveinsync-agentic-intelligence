# docker-compose `frontend` service (plan §9). Build-time VITE_API_BASE_URL/
# VITE_USE_MOCK (Vite bakes import.meta.env.* into the bundle at build time,
# not read at container runtime) are passed as build args from
# docker-compose.yml so this image can point at whatever the compose
# network's backend service is actually reachable at.
FROM node:20-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./

# Relative, not an absolute localhost/hostname URL: the runtime stage below
# proxies /api on the SAME origin that serves the page (see Caddyfile), so
# this resolves correctly whether that origin is localhost:5173 or a
# Cloudflare Quick Tunnel URL -- no rebuild needed to go from local to public.
ARG VITE_API_BASE_URL=/api
ARG VITE_USE_MOCK=false
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_USE_MOCK=${VITE_USE_MOCK}
RUN npm run build

# Caddy, not a plain static server (`serve`): a Cloudflare Quick Tunnel only
# takes one --url target, so the single container the tunnel points at must
# both serve the built app AND reverse-proxy /api (incl. the /api/ws
# WebSocket, which Caddy's reverse_proxy upgrades automatically) to the
# backend service -- see frontend/Caddyfile.
FROM caddy:2-alpine
COPY --from=build /app/dist /srv
COPY frontend/Caddyfile /etc/caddy/Caddyfile
EXPOSE 4173
