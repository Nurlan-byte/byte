# Exact toolchain tags; pin these build args to registry digests before hosting.
ARG NODE_IMAGE=node:24.19.0-bookworm-slim
ARG PYTHON_IMAGE=python:3.14.8-slim-bookworm
ARG CADDY_IMAGE=caddy:2.10.2-alpine

FROM ${NODE_IMAGE} AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run typecheck && npm run build

FROM ${PYTHON_IMAGE} AS api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BAKEPLAN_DB=/data/bakeplan.sqlite3
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN python -m pip install --no-cache-dir -r backend/requirements.lock \
    && groupadd --gid 10001 bakeplan \
    && useradd --uid 10001 --gid 10001 --no-create-home bakeplan \
    && mkdir /data /backups \
    && chown 10001:10001 /data /backups
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)); assert r['status']=='ok' and r['db_status']=='ok'"]
# Deliberately one worker: one global demo, one SQLite file, bounded training.
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log"]

FROM ${CADDY_IMAGE} AS gateway
COPY --from=frontend-build /build/frontend/dist /srv
COPY deploy/Caddyfile.local /etc/caddy/Caddyfile
COPY deploy/Caddyfile.https /etc/caddy/Caddyfile.https
COPY deploy/routes.caddy /etc/caddy/routes.caddy
COPY deploy/gateway-entrypoint.sh /usr/local/bin/bakeplan-gateway
ENTRYPOINT ["/usr/local/bin/bakeplan-gateway"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
