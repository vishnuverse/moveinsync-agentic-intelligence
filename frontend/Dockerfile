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

ARG VITE_API_BASE_URL=http://localhost:8000/api
ARG VITE_USE_MOCK=false
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_USE_MOCK=${VITE_USE_MOCK}
RUN npm run build

FROM node:20-slim
WORKDIR /app
RUN npm install -g serve@14
COPY --from=build /app/dist ./dist
EXPOSE 4173
CMD ["serve", "-s", "dist", "-l", "4173"]
