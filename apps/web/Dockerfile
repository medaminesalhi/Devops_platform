FROM nginx:alpine

LABEL org.opencontainers.image.title="SApixi Platform Web"
LABEL org.opencontainers.image.description="Web interface for the SApixi deployment platform"

COPY src/index.html /usr/share/nginx/html/index.html
COPY src/healthz /usr/share/nginx/html/healthz

EXPOSE 80
