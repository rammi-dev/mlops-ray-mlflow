#!/usr/bin/env bash
# Quick-look status of the kyper-framework minikube cluster.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require_all

if ! cluster_exists; then
  warn "cluster '${CLUSTER_NAME}' does not exist — run ./bootstrap.sh"
  exit 1
fi

log "minikube status"
minikube status -p "${CLUSTER_NAME}" || true

echo
log "nodes with role labels"
kubectl get nodes -L "${LABEL_KEY}" -o wide

echo
log "platform namespace (ds-platform)"
kubectl get all -n ds-platform 2>/dev/null || warn "ds-platform namespace not yet present"

echo
log "workload namespace (ds-workloads)"
kubectl get all -n ds-workloads 2>/dev/null || warn "ds-workloads namespace not yet present"
