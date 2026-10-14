# Build static operator console, then serve via Nginx (proxies /api and /health).
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Same-origin API in production: empty base URL (overrides any localhost .env).
# Browser auth uses HttpOnly session cookies — never bake API tokens into the bundle.
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=

RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/nginx-health || wget -q -O /dev/null http://127.0.0.1/ || exit 1
