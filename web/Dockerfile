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

FROM nginx:1.29-alpine AS runtime

# nginx's own binary links only libpcre2, libssl, libcrypto and libz (confirmed with `ldd`); the
# rest of the base image's packages are optional nginx modules this deployment never loads
# (image-filter, njs, xslt — nothing here needs image processing, JavaScript scripting or XSLT)
# and their own dependency trees (curl and friends), carried along for the ride. Purging them
# drops the image from 72 packages to 26 and closes every CVE those packages accumulate, rather
# than tracking each one individually as it's disclosed. The four libraries nginx does link
# against are upgraded first, since the base image's own baked-in index can lag the actual
# package repository by the time this builds.
RUN apk update \
    && apk upgrade --no-cache \
    && apk del --no-cache \
      curl nginx-module-image-filter nginx-module-njs nginx-module-xslt \
      c-ares libxml2 nghttp2-libs util-linux libuuid \
    && nginx -t

COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD wget -qO- http://localhost:80/ >/dev/null || exit 1
