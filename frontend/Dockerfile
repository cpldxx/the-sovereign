# Sovereign web: the UI built once, served by nginx, which also forwards /api/* to the services — one origin, one port.
# Build context: the repository root.
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_LANGFUSE_URL=""
ENV VITE_KG_URL=/api/kg VITE_HERMES_URL=/api/hermes VITE_RESEARCH_URL=/api/research \
    VITE_LANGFUSE_URL=$VITE_LANGFUSE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
