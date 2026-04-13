#!/usr/bin/env bash
# Seed the shared oxy-data NFS PVC with local oxy/ contents.
# Prereqs: deploy-nfs.sh has run, and the chart has been (re)deployed so the
# PV/PVC objects exist in ds-platform and ds-workloads.
#
# Usage: ./seed-oxy-nfs.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OXY_ROOT="$(cd "${SCRIPT_DIR}/../../../oxy" && pwd)"
SEED_NS="ds-workloads"
PVC="oxy-data"
HELPER_POD="oxy-seeder"

echo "Launching helper pod in ${SEED_NS} that mounts PVC ${PVC}..."
kubectl delete pod -n "${SEED_NS}" "${HELPER_POD}" --ignore-not-found >/dev/null
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${HELPER_POD}
  namespace: ${SEED_NS}
spec:
  restartPolicy: Never
  containers:
    - name: seeder
      image: busybox
      command: ["sh", "-c", "sleep 3600"]
      volumeMounts:
        - name: oxy
          mountPath: /mnt/oxy
  volumes:
    - name: oxy
      persistentVolumeClaim:
        claimName: ${PVC}
EOF

kubectl wait --for=condition=Ready pod/${HELPER_POD} -n "${SEED_NS}" --timeout=120s

echo "Copying ${OXY_ROOT}/ -> ${SEED_NS}/${HELPER_POD}:/mnt/oxy/ ..."
kubectl cp "${OXY_ROOT}/." "${SEED_NS}/${HELPER_POD}:/mnt/oxy/"

echo "Verifying contents..."
kubectl exec -n "${SEED_NS}" "${HELPER_POD}" -- ls -la /mnt/oxy

echo "Cleaning up helper pod..."
kubectl delete pod -n "${SEED_NS}" "${HELPER_POD}" --ignore-not-found

echo "Seed complete. oxy-data PVC in ds-workloads is populated."
