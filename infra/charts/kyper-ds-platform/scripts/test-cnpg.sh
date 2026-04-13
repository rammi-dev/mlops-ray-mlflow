#!/usr/bin/env bash
# Smoke-test CNPG Postgres for MLflow:
#   1. Verify the CNPG Cluster CR is healthy
#   2. Verify the app secret exists with connection details
#   3. Connect to Postgres and run a query
#
# Usage:  ./test-cnpg.sh

set -euo pipefail

NS="ds-platform"
CLUSTER_NAME="kyper-ds-platform-mlflow-pg"
TIMEOUT=120

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. CNPG Cluster health ───────────────────────────────────────────────────

log "checking CNPG Cluster '${CLUSTER_NAME}' status"
STATUS=$(kubectl get cluster.postgresql.cnpg.io "${CLUSTER_NAME}" -n "${NS}" \
  -o jsonpath='{.status.phase}' 2>/dev/null) || die "Cluster CR not found"

if [ "${STATUS}" != "Cluster in healthy state" ]; then
  die "cluster not healthy — status: ${STATUS}"
fi
log "cluster status: ${STATUS}"

READY=$(kubectl get cluster.postgresql.cnpg.io "${CLUSTER_NAME}" -n "${NS}" \
  -o jsonpath='{.status.readyInstances}')
INSTANCES=$(kubectl get cluster.postgresql.cnpg.io "${CLUSTER_NAME}" -n "${NS}" \
  -o jsonpath='{.status.instances}')
log "instances: ${READY}/${INSTANCES} ready"

# ── 2. App secret exists ─────────────────────────────────────────────────────

APP_SECRET="${CLUSTER_NAME}-app"
log "verifying app secret '${APP_SECRET}'"
kubectl get secret "${APP_SECRET}" -n "${NS}" >/dev/null 2>&1 \
  || die "app secret '${APP_SECRET}' not found"

# Extract connection details
DB_HOST=$(kubectl get secret "${APP_SECRET}" -n "${NS}" -o jsonpath='{.data.host}' | base64 -d)
DB_PORT=$(kubectl get secret "${APP_SECRET}" -n "${NS}" -o jsonpath='{.data.port}' | base64 -d)
DB_NAME=$(kubectl get secret "${APP_SECRET}" -n "${NS}" -o jsonpath='{.data.dbname}' | base64 -d)
DB_USER=$(kubectl get secret "${APP_SECRET}" -n "${NS}" -o jsonpath='{.data.user}' | base64 -d)
log "connection: ${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# ── 3. Run a query inside the Postgres pod ────────────────────────────────────

PRIMARY_POD=$(kubectl get cluster.postgresql.cnpg.io "${CLUSTER_NAME}" -n "${NS}" \
  -o jsonpath='{.status.currentPrimary}')
log "primary pod: ${PRIMARY_POD}"

log "running test query"
OUTPUT=$(kubectl exec "${PRIMARY_POD}" -n "${NS}" -c postgres -- \
  psql -U postgres -d "${DB_NAME}" -t -c "SELECT 'CNPG_SMOKE_OK'" 2>&1) || true

echo "${OUTPUT}"

if echo "${OUTPUT}" | grep -q "CNPG_SMOKE_OK"; then
  log "all tests passed — CNPG Postgres is functional"
else
  die "CNPG_SMOKE_OK not found in query output"
fi
