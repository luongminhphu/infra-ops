# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx-include.conf /etc/nginx/conf.d/default.conf
COPY nginx-security-headers.conf /etc/nginx/security-headers.conf
COPY nginx.conf.template /etc/nginx/templates/infra-ops.conf.template
COPY --chmod=0755 docker-tls.sh /docker-entrypoint.d/40-infra-ops-tls.sh
ENV API_UPSTREAM=api:8000 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    NGINX_ENVSUBST_OUTPUT_DIR=/tmp
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080 8443
