#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="ds-platform"
SERVICE="proxy-public"
LOCAL_PORT="${1:-8080}"

echo "══════════════════════════════════════════════════════════"
echo "  JupyterHub Access"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  URL:      http://localhost:${LOCAL_PORT}"
echo "  Username: any (DummyAuthenticator)"
echo "  Password: changeme"
echo ""
echo "  Port-forwarding ${SERVICE} → localhost:${LOCAL_PORT}"
echo "  Press Ctrl+C to stop"
echo "══════════════════════════════════════════════════════════"
echo ""

kubectl port-forward "svc/${SERVICE}" -n "${NAMESPACE}" "${LOCAL_PORT}:80"
