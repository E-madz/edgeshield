# syntax=docker/dockerfile:1

# ---- stage 1: build the Vue app into static files ----
FROM node:22-alpine AS build
# Optional npm mirror for slow or restricted networks (set NPM_REGISTRY in .env).
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm config set registry "$NPM_REGISTRY" \
    && npm config set fetch-retries 5 \
    && npm config set fetch-timeout 120000 \
    && npm ci
COPY . .
RUN npm run build

# ---- stage 2: nginx serves the files and proxies /api to Django ----
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
