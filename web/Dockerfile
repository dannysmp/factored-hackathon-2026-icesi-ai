# =============================================================================
# Dockerfile — web static build image
# =============================================================================
# Purpose:
#   The built React app (`npm run build`), served as static files. No
#   runtime Node process and no dev dependency ships to the deployed host.
# Design:
#   Multi-stage: Node builds the production bundle in the first stage; the
#   final stage is nginx serving only `dist/`, on an unprivileged port
#   behind Caddy, which is the one place TLS terminates.
# Usage:
#   docker build -t dispute-intake-web .
#   docker run --rm -p 8080:80 dispute-intake-web
# =============================================================================

FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD wget -qO- http://localhost:80/ >/dev/null || exit 1
