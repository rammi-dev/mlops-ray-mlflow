#!/usr/bin/env bash
# Port-forward the CNPG Postgres cluster to localhost.
#
# Usage:
#   ./access-postgres.sh              # port-forward on localhost:5432
#   ./access-postgres.sh 15432        # port-forward on localhost:15432
#
# Connection string printed on start. Ctrl-C to stop.

set -euo pipefail

NS="ds-platform"
CLUSTER_NAME="kyper-ds-platform-mlflow-pg"
LOCAL_PORT="${1:-5432}"

log() { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

# Resolve primary pod
PRIMARY=$(kubectl get cluster.postgresql.cnpg.io "${CLUSTER_NAME}" -n "${NS}" \
  -o jsonpath='{.status.currentPrimary}' 2>/dev/null) \
  || die "CNPG cluster '${CLUSTER_NAME}' not found in namespace '${NS}'"

# Read credentials from the CNPG-generated app secret
APP_SECRET="${CLUSTER_NAME}-app"
DB_USER=$(kubectl get secret "${APP_SECRET}" -n "${NS}" -o jsonpath='{.data.user}' | base64 -d)
DB_PASS=$(kubectl get secret "${APP_SECRET}" -n "${NS}" -o jsonpath='{.data.password}' | base64 -d)
DB_NAME=$(kubectl get secret "${APP_SECRET}" -n "${NS}" -o jsonpath='{.data.dbname}' | base64 -d)

log "primary pod: ${PRIMARY}"
log "forwarding ${PRIMARY}:5432 → localhost:${LOCAL_PORT}"
echo ""
echo "  psql:"
echo "    PGPASSWORD='${DB_PASS}' psql -h 127.0.0.1 -p ${LOCAL_PORT} -U ${DB_USER} -d ${DB_NAME}"
echo ""
echo "  connection URI:"
echo "    postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:${LOCAL_PORT}/${DB_NAME}"
echo ""
echo "  Ctrl-C to stop"
echo ""

kubectl port-forward "pod/${PRIMARY}" "${LOCAL_PORT}:5432" -n "${NS}"
