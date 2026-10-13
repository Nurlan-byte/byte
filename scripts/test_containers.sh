#!/usr/bin/env bash
# Isolated Linux CI/local Docker check. Never run against an existing deployment.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v docker >/dev/null
command -v python3 >/dev/null
docker compose version
docker info >/dev/null
private_dir=$(mktemp -d)
project="bakeplan-ci-$$"
export BAKEPLAN_AUTH_FILE="$private_dir/demo_users"
export BAKEPLAN_RELEASE="ci-$$"
export BAKEPLAN_DB_FILE="bakeplan.sqlite3"
export BAKEPLAN_DOMAIN="example.invalid"
export BAKEPLAN_ACME_EMAIL="ci@example.invalid"
compose=(docker compose --project-name "$project" -f deploy/compose.yaml)
cleanup() {
    result=$?
    if [ "$result" -ne 0 ]; then
        "${compose[@]}" logs --no-color || true
    fi
    # Only this disposable project; NEVER use this cleanup on an operator demo.
    "${compose[@]}" down --volumes --remove-orphans || true
    rm -rf "$private_dir"
    exit "$result"
}
trap cleanup EXIT
umask 077
: > "$BAKEPLAN_AUTH_FILE"
"${compose[@]}" config --quiet
"${compose[@]}" -f deploy/compose.https.yaml config --quiet
"${compose[@]}" build

# No secret mounted: startup must fail before Caddy can serve any route.
if docker run --rm "bakeplan-gateway:$BAKEPLAN_RELEASE" true; then
    echo 'Gateway unexpectedly ran without an access-control secret' >&2
    exit 1
fi
# Explicit public, disposable CI fixture, not a generated real password.
hash=$(docker run --rm --entrypoint caddy "bakeplan-gateway:$BAKEPLAN_RELEASE" hash-password --plaintext public-test-fixture-never-reuse)
printf 'bakeplan-ci-only %s\n' "$hash" > "$BAKEPLAN_AUTH_FILE"
# The enclosing host directory is 0700. Caddy has no DAC_OVERRIDE capability;
# file must remain readable after Compose bind-mounts it into /run/secrets.
chmod 644 "$BAKEPLAN_AUTH_FILE"
"${compose[@]}" run --rm --no-deps gateway caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
"${compose[@]}" -f deploy/compose.https.yaml run --rm --no-deps gateway caddy validate --config /etc/caddy/Caddyfile.https --adapter caddyfile

"${compose[@]}" up -d --wait --wait-timeout 90
# No API bypass may be published to the host.
if [ -n "$("${compose[@]}" port api 8000 2>/dev/null || true)" ]; then
    echo 'Backend port must not be host-published' >&2
    exit 1
fi
python3 -m scripts.container_fixture probe
python3 -m scripts.container_fixture seed --state "$private_dir/state.json"
"${compose[@]}" restart api
"${compose[@]}" up -d --wait --wait-timeout 90
python3 -m scripts.container_fixture verify --state "$private_dir/state.json"

"${compose[@]}" exec -T api python -m scripts.sqlite_snapshot backup --output /backups/ci.sqlite3
"${compose[@]}" exec -T api python -m scripts.sqlite_snapshot verify /backups/ci.sqlite3
"${compose[@]}" stop gateway api
"${compose[@]}" run --rm --no-deps api python -m scripts.sqlite_snapshot restore /backups/ci.sqlite3 --database /data/restored.sqlite3 --app-stopped
export BAKEPLAN_DB_FILE="restored.sqlite3"
"${compose[@]}" up -d --wait --wait-timeout 90
python3 -m scripts.container_fixture probe
python3 -m scripts.container_fixture verify --state "$private_dir/state.json"
echo 'Container loopback auth, synthetic workflow, restart, backup and restore checks passed.'
echo 'Public DNS, trusted HTTPS issuance/renewal, real hosting and manual browser QA remain separate.'
