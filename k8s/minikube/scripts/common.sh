#!/usr/bin/env bash
# Shared config + helpers for the minikube bootstrap/destroy/status scripts.
# Source this file; do not execute directly.

set -euo pipefail

# ── Cluster shape ─────────────────────────────────────────────────────────────
#
# 3 nodes, uniform in minikube (per-node sizing is not supported). "Small"
# vs "workload" is expressed via node labels + nodeSelector in the Helm chart,
# not by physical size.

CLUSTER_NAME="${KYP_CLUSTER_NAME:-kyper-dev}"
K8S_VERSION="${KYP_K8S_VERSION:-v1.34.0}"
DRIVER="${KYP_DRIVER:-docker}"

TOTAL_NODES=3              # 1 control-plane + 2 workers
NODE_CPUS="${KYP_NODE_CPUS:-4}"
NODE_MEMORY_MB="${KYP_NODE_MEMORY_MB:-6144}"
NODE_DISK_GB="${KYP_NODE_DISK_GB:-40}"

# Label conventions consumed by Helm values (nodeSelector):
#   kyper.ai/role=platform   — ds-platform services
#   kyper.ai/role=workload   — ds-workloads RayJobs / dev-pods
LABEL_KEY="kyper.ai/role"
LABEL_PLATFORM="platform"
LABEL_WORKLOAD="workload"

# Node-role assignment strategy:
#   <cluster>            → platform (the minikube control-plane; it also schedules pods)
#   <cluster>-m02        → platform
#   <cluster>-m03        → workload
PLATFORM_NODES=("${CLUSTER_NAME}" "${CLUSTER_NAME}-m02")
WORKLOAD_NODES=("${CLUSTER_NAME}-m03")

# Namespaces created during bootstrap
NS_PLATFORM="ds-platform"
NS_WORKLOADS="ds-workloads"

ADDONS=(ingress metrics-server csi-hostpath-driver)

# ── Helpers ───────────────────────────────────────────────────────────────────

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_all() {
  require_cmd minikube
  require_cmd kubectl
}

cluster_exists() {
  minikube profile list -o json 2>/dev/null \
    | grep -q "\"Name\":\s*\"${CLUSTER_NAME}\""
}

cluster_running() {
  [ "$(minikube status -p "${CLUSTER_NAME}" -f '{{.Host}}' 2>/dev/null || true)" = "Running" ]
}
