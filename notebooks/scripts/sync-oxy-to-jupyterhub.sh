#!/usr/bin/env bash
# Sync the anomaly-detection notebook into a JupyterHub user's PVC
# and clean stale oxy files (code & data now served via NFS PVC at /mnt/oxy).
#
# Usage:
#   ./sync-oxy-to-jupyterhub.sh [username]
#
# Requires: a running JupyterHub singleuser pod for the given user.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
USERNAME="${1:-admin}"
NS="ds-workloads"

# ── Resolve the singleuser pod ───────────────────────────────────────────────
POD="jupyter-${USERNAME}"

if ! kubectl get pod "${POD}" -n "${NS}" &>/dev/null; then
    echo "ERROR: Pod '${POD}' not found in namespace '${NS}'"
    echo ""
    echo "Make sure:"
    echo "  1. JupyterHub is running   (kubectl get pods -n ${NS})"
    echo "  2. User '${USERNAME}' has a running server (log in at JupyterHub UI)"
    exit 1
fi

echo "Found singleuser pod: ${POD} (user=${USERNAME})"

# ── Detect home dir and user inside the pod ──────────────────────────────────
HOME_DIR=$(kubectl exec -n "${NS}" "${POD}" -- sh -c 'echo $HOME')
POD_USER=$(kubectl exec -n "${NS}" "${POD}" -- id -u)
POD_GROUP=$(kubectl exec -n "${NS}" "${POD}" -- id -g)
echo "Pod home: ${HOME_DIR}  uid:gid=${POD_USER}:${POD_GROUP}"

fix_perms() {
    kubectl exec -n "${NS}" "${POD}" -- chown -R "${POD_USER}:${POD_GROUP}" "$1"
}

# ── Ensure oxy symlink in home points to the NFS mount ───────────────────────
kubectl exec -n "${NS}" "${POD}" -- ln -sfn /mnt/oxy "${HOME_DIR}/oxy"

# ── Ensure results directory exists on PVC ───────────────────────────────────
echo "Creating results directories on PVC..."
kubectl exec -n "${NS}" "${POD}" -- mkdir -p \
    "${HOME_DIR}/results/anomaly-detection/features" \
    "${HOME_DIR}/results/anomaly-detection/detections"
fix_perms "${HOME_DIR}/results"

# ── Copy the notebook ────────────────────────────────────────────────────────
echo "Copying notebook..."
kubectl cp "${SCRIPT_DIR}/../anomaly_detection_ray_parallel.ipynb" \
    "${NS}/${POD}:${HOME_DIR}/anomaly_detection_ray_parallel.ipynb"
fix_perms "${HOME_DIR}/anomaly_detection_ray_parallel.ipynb"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  Sync complete!"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  Pod:      ${POD}"
echo "  Notebook: ${HOME_DIR}/anomaly_detection_ray_parallel.ipynb"
echo "  Code/data: /mnt/oxy (NFS PVC, read-only)"
echo "  Results:   ${HOME_DIR}/results/ (PVC, writable)"
echo ""
echo "  Open the notebook from JupyterLab and run all cells."
echo "══════════════════════════════════════════════════════════"
