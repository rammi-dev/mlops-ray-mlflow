#!/usr/bin/env bash
# Bootstrap a 3-node minikube cluster for kyper-framework dev.
#
#   2 nodes labeled  kyper.ai/role=platform   (→ ds-platform services)
#   1 node  labeled  kyper.ai/role=workload   (→ ds-workloads jobs)
#
# Idempotent: re-running on an existing cluster just re-applies labels/addons.
#
# Usage:   ./bootstrap.sh
# Env:     KYP_CLUSTER_NAME, KYP_NODE_CPUS, KYP_NODE_MEMORY_MB, KYP_DRIVER, KYP_K8S_VERSION

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require_all

# ── 1. Create the cluster (or reuse existing) ─────────────────────────────────

if cluster_exists; then
  log "cluster '${CLUSTER_NAME}' already exists — reusing"
  if ! cluster_running; then
    log "cluster is stopped — starting"
    minikube start -p "${CLUSTER_NAME}"
  fi
else
  log "creating minikube cluster '${CLUSTER_NAME}' (${TOTAL_NODES} nodes, ${NODE_CPUS} CPU / ${NODE_MEMORY_MB} MiB each)"
  minikube start \
    -p "${CLUSTER_NAME}" \
    --driver="${DRIVER}" \
    --kubernetes-version="${K8S_VERSION}" \
    --nodes="${TOTAL_NODES}" \
    --cpus="${NODE_CPUS}" \
    --memory="${NODE_MEMORY_MB}" \
    --disk-size="${NODE_DISK_GB}g"
fi

kubectl config use-context "${CLUSTER_NAME}" >/dev/null

# ── 1b. Ensure we have TOTAL_NODES nodes ──────────────────────────────────────
#
# `minikube start -p <p>` on an existing profile does NOT add nodes — it only
# boots the existing set. If the profile was created with fewer nodes (e.g.
# a previous bootstrap with different TOTAL_NODES, or if `minikube node
# delete` was used), top it up here.

current_nodes=$(minikube -p "${CLUSTER_NAME}" node list 2>/dev/null | wc -l)
if [ "${current_nodes}" -lt "${TOTAL_NODES}" ]; then
  missing=$((TOTAL_NODES - current_nodes))
  log "cluster has ${current_nodes} node(s); adding ${missing} more"
  for _ in $(seq 1 "${missing}"); do
    minikube -p "${CLUSTER_NAME}" node add --worker
  done
elif [ "${current_nodes}" -gt "${TOTAL_NODES}" ]; then
  warn "cluster has ${current_nodes} nodes but TOTAL_NODES=${TOTAL_NODES} — not removing; adjust manually if needed"
fi

# ── 2. Label nodes ────────────────────────────────────────────────────────────
#
# kubectl label ... --overwrite is idempotent; safe to re-run.

log "waiting for all ${TOTAL_NODES} nodes to be Ready"
kubectl wait --for=condition=Ready "nodes" --all --timeout=300s >/dev/null

log "labeling platform nodes: ${PLATFORM_NODES[*]}"
for n in "${PLATFORM_NODES[@]}"; do
  kubectl label node "${n}" "${LABEL_KEY}=${LABEL_PLATFORM}" --overwrite >/dev/null
done

log "labeling workload nodes: ${WORKLOAD_NODES[*]}"
for n in "${WORKLOAD_NODES[@]}"; do
  kubectl label node "${n}" "${LABEL_KEY}=${LABEL_WORKLOAD}" --overwrite >/dev/null
done

# ── 3. Create namespaces ──────────────────────────────────────────────────────

for ns in "${NS_PLATFORM}" "${NS_WORKLOADS}"; do
  kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  log "namespace '${ns}' ready"
done

# ── 4. Optional: taint the workload node so only tolerating pods land there ───
#
# Commented out by default — enable if you want hard isolation. Off by default
# so ad-hoc `kubectl run` still works without adding tolerations.
#
# for n in "${WORKLOAD_NODES[@]}"; do
#   kubectl taint node "${n}" "${LABEL_KEY}=${LABEL_WORKLOAD}:NoSchedule" --overwrite >/dev/null || true
# done

# ── 5. Enable addons ──────────────────────────────────────────────────────────

for addon in "${ADDONS[@]}"; do
  log "enabling addon: ${addon}"
  minikube addons enable "${addon}" -p "${CLUSTER_NAME}" >/dev/null
done

# ── 6. Summary ────────────────────────────────────────────────────────────────

log "cluster is up. nodes + labels:"
kubectl get nodes -L "${LABEL_KEY}" -o wide

cat <<EOF

─── next steps ──────────────────────────────────────────────────────────────

  # Verify node allocation:
  kubectl get nodes -L ${LABEL_KEY}

  # Install the platform chart (once Phase-1 chart exists):
  helm upgrade --install ds-platform \\
    ${SCRIPT_DIR}/../../../infra/charts/kyper-ds-platform \\
    -f ${SCRIPT_DIR}/../../../infra/charts/kyper-ds-platform/values-minikube.yaml \\
    --create-namespace -n ds-platform

  # Tunnel to reach ingress:
  minikube tunnel -p ${CLUSTER_NAME} &

─────────────────────────────────────────────────────────────────────────────
EOF
