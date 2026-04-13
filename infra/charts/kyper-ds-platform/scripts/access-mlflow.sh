#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="ds-platform"
SERVICE="mlflow"
LOCAL_PORT="${1:-5000}"

echo "══════════════════════════════════════════════════════════"
echo "  MLflow Tracking Server"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  URL: http://localhost:${LOCAL_PORT}"
echo ""
echo "  In-cluster URI (already wired via MLFLOW_TRACKING_URI):"
echo "    http://mlflow.${NAMESPACE}.svc.cluster.local:5000"
echo ""
echo "  Port-forwarding ${SERVICE} → localhost:${LOCAL_PORT}"
echo "  Press Ctrl+C to stop"
echo "══════════════════════════════════════════════════════════"
echo ""

kubectl port-forward "svc/${SERVICE}" -n "${NAMESPACE}" "${LOCAL_PORT}:5000"
