# syntax=docker/dockerfile:1

# Build the browser bundle once, then serve only the generated static files.
# The runtime image intentionally contains no Node.js, package manager, or
# source tree: it is a small, architecture-neutral nginx process.
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build


# nginxinc/nginx-unprivileged listens on 8080 and runs without root. This
# avoids a privileged web server in the Pi container while keeping the host
# port bound to loopback by the deployment script.
FROM nginxinc/nginx-unprivileged:1.29-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
