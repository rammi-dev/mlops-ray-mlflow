#!/usr/bin/env bash
# Smoke-test the MLflow tracking server:
#   1. Verify the deployment is Ready
#   2. Hit the /health endpoint
#   3. Verify Postgres connectivity via the /api/2.0/mlflow/experiments/search endpoint
#
# Usage:  ./test-mlflow.sh

set -euo pipefail

NS="ds-platform"
DEPLOY="mlflow"
TIMEOUT=120

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. Deployment ready ──────────────────────────────────────────────────────

log "checking MLflow deployment"
kubectl wait --for=condition=Available deploy/${DEPLOY} -n "${NS}" --timeout="${TIMEOUT}s" \
  || die "MLflow deployment not available"

POD=$(kubectl get pod -l app.kubernetes.io/name=mlflow -n "${NS}" \
  -o jsonpath='{.items[0].metadata.name}')
log "pod: ${POD}"

# ── 2. Health check ──────────────────────────────────────────────────────────

log "checking /health endpoint"
HEALTH=$(kubectl exec "${POD}" -n "${NS}" -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:5000/health').read().decode())" 2>&1) || true
echo "  health: ${HEALTH}"
if [ -z "${HEALTH}" ] || ! echo "${HEALTH}" | grep -q "OK"; then
  die "/health did not return OK"
fi

# ── 3. Use MLflow client (proves Postgres backend is working) ────────────────

log "testing MLflow client (verifies Postgres backend)"
OUTPUT=$(kubectl exec "${POD}" -n "${NS}" -- \
  python -c "
import mlflow
mlflow.set_tracking_uri('http://localhost:5000')
client = mlflow.MlflowClient()
experiments = client.search_experiments()
print('experiments:', [e.name for e in experiments])
print('MLFLOW_SMOKE_OK')
" 2>&1) || true
echo "  ${OUTPUT}"

if echo "${OUTPUT}" | grep -q "MLFLOW_SMOKE_OK"; then
  log "all tests passed — MLflow tracking server is functional"
else
  die "MLFLOW_SMOKE_OK not found in output"
fi
