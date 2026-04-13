#!/usr/bin/env bash
# Smoke-test the KubeRay operator:
#   1. Create a RayCluster on the workload node
#   2. Submit a job via the Ray Jobs API (kubectl exec)
#   3. Verify output
#   4. Tear down
#
# Usage:  ./test-kuberay.sh

set -euo pipefail

NS="ds-workloads"
CLUSTER_NAME="test-raycluster"
RAY_IMAGE="rayproject/ray:2.41.0"
TIMEOUT=180

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; cleanup; exit 1; }

cleanup() {
  log "cleaning up test resources"
  kubectl delete raycluster "${CLUSTER_NAME}" -n "${NS}" --ignore-not-found >/dev/null 2>&1
}
trap cleanup EXIT

# ── 1. Create RayCluster ──────────────────────────────────────────────────────

log "creating RayCluster '${CLUSTER_NAME}' on workload node"
kubectl apply -n "${NS}" -f - <<EOF
apiVersion: ray.io/v1
kind: RayCluster
metadata:
  name: ${CLUSTER_NAME}
spec:
  rayVersion: "2.41.0"
  enableInTreeAutoscaling: true
  headGroupSpec:
    rayStartParams:
      dashboard-host: "0.0.0.0"
    template:
      spec:
        serviceAccountName: ray-worker
        nodeSelector:
          kyper.ai/role: workload
        containers:
          - name: ray-head
            image: ${RAY_IMAGE}
            resources:
              requests: { cpu: "500m", memory: "1Gi" }
              limits:   { cpu: "1",    memory: "2Gi" }
            ports:
              - containerPort: 6379
              - containerPort: 8265
  workerGroupSpecs:
    - groupName: workers
      replicas: 0
      minReplicas: 0
      maxReplicas: 2
      rayStartParams: {}
      template:
        spec:
          serviceAccountName: ray-worker
          nodeSelector:
            kyper.ai/role: workload
          containers:
            - name: ray-worker
              image: ${RAY_IMAGE}
              resources:
                requests: { cpu: "500m", memory: "512Mi" }
                limits:   { cpu: "1",    memory: "1Gi" }
EOF

log "waiting for head pod to exist"
elapsed=0
while [ $elapsed -lt $TIMEOUT ]; do
  COUNT=$(kubectl get pods -l ray.io/cluster=${CLUSTER_NAME},ray.io/node-type=head \
    -n "${NS}" --no-headers 2>/dev/null | wc -l)
  if [ "$COUNT" -gt 0 ]; then break; fi
  sleep 2
  elapsed=$((elapsed + 2))
done

log "waiting for head pod to be Ready (up to ${TIMEOUT}s)"
kubectl wait --for=condition=Ready pod \
  -l ray.io/cluster=${CLUSTER_NAME},ray.io/node-type=head \
  -n "${NS}" --timeout="${TIMEOUT}s"

HEAD_POD=$(kubectl get pod -l ray.io/cluster=${CLUSTER_NAME},ray.io/node-type=head \
  -n "${NS}" -o jsonpath='{.items[0].metadata.name}')
HEAD_NODE=$(kubectl get pod "${HEAD_POD}" -n "${NS}" -o jsonpath='{.spec.nodeName}')
log "head pod '${HEAD_POD}' running on node: ${HEAD_NODE}"

# ── 2. Submit job via kubectl exec ────────────────────────────────────────────

log "submitting job to Ray head"
OUTPUT=$(kubectl exec "${HEAD_POD}" -n "${NS}" -c ray-head -- \
  python -c "
import ray, time
ray.init()
print('resources before:', ray.cluster_resources())

@ray.remote(num_cpus=1)
def work(x):
    import socket, time
    time.sleep(20)   # long enough so workers join before head drains the queue
    return {'value': x * x, 'node': socket.gethostname()}

# submit 4 tasks x 1 CPU — head has ~1 CPU, autoscaler will add workers
print('submitting tasks (autoscaler will add workers)...')
refs = [work.remote(i) for i in range(4)]
results = ray.get(refs, timeout=300)

nodes = set(r['node'] for r in results)
values = [r['value'] for r in results]
print('results:', values)
print('nodes used:', nodes)
print('resources after:', ray.cluster_resources())
assert sorted(values) == [0, 1, 4, 9], f'unexpected values: {values}'
assert len(nodes) > 1, f'expected multiple nodes, got: {nodes}'
print('SMOKE_TEST_OK')
" 2>&1) || true

echo "${OUTPUT}"

# ── 3. Show pods (workers should have scaled up) ─────────────────────────────

log "pods in cluster:"
kubectl get pods -n "${NS}" -l ray.io/cluster=${CLUSTER_NAME} -o wide

# ── 4. Verify output ─────────────────────────────────────────────────────────

if echo "${OUTPUT}" | grep -q "SMOKE_TEST_OK"; then
  log "all tests passed — KubeRay operator is functional"
else
  die "SMOKE_TEST_OK not found in output"
fi
