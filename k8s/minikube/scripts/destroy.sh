#!/usr/bin/env bash
# Tear down the kyper-framework minikube cluster.
#
# Usage:  ./destroy.sh          # prompts for confirmation
#         ./destroy.sh --yes    # non-interactive

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require_all

if ! cluster_exists; then
  log "cluster '${CLUSTER_NAME}' does not exist — nothing to do"
  exit 0
fi

if [ "${1:-}" != "--yes" ]; then
  read -r -p "Delete minikube cluster '${CLUSTER_NAME}'? [y/N] " ans
  case "${ans}" in
    y|Y|yes|YES) ;;
    *) die "aborted" ;;
  esac
fi

log "deleting cluster '${CLUSTER_NAME}'"
minikube delete -p "${CLUSTER_NAME}"
log "done"
