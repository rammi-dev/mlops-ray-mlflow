# k8s/minikube

Local minikube bootstrap for kyper-framework dev.

## Cluster shape

3 nodes. Per-node sizing is uniform (minikube limitation); role segregation is by **node label**, enforced downstream by `nodeSelector` in the Helm chart.

| Role | Label | Nodes (default names) | Purpose |
|---|---|---|---|
| platform | `kyper.ai/role=platform` | `kyper-dev`, `kyper-dev-m02` | CNPG, MLflow, KubeRay operator |
| workload | `kyper.ai/role=workload` | `kyper-dev-m03` | RayJobs, dev-pods |

Default sizing: **4 CPU / 6 GiB / 40 GB disk** per node. Tune via env:

```bash
KYP_NODE_CPUS=4 KYP_NODE_MEMORY_MB=6144 ./scripts/bootstrap.sh
```

## Scripts

| Script | What it does |
|---|---|
| [`scripts/bootstrap.sh`](scripts/bootstrap.sh) | Create (or reuse) 3-node cluster, wait for Ready, apply role labels, enable `ingress` + `metrics-server` |
| [`scripts/destroy.sh`](scripts/destroy.sh) | Delete the cluster (`--yes` skips confirmation) |
| [`scripts/status.sh`](scripts/status.sh) | Cluster + node labels + `ds-platform` / `ds-workloads` summary |
| [`scripts/common.sh`](scripts/common.sh) | Shared config (cluster name, sizing, label constants) — sourced by the others |

All scripts are idempotent. Re-running `bootstrap.sh` on an existing cluster re-applies labels and addons without recreating nodes.

## Usage

```bash
# Initial bring-up
./scripts/bootstrap.sh

# Inspect
./scripts/status.sh

# Tear down
./scripts/destroy.sh
```

After bootstrap, install the platform chart (see [`../../infra/README.md`](../../infra/README.md)).

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `KYP_CLUSTER_NAME` | `kyper-dev` | minikube profile name |
| `KYP_K8S_VERSION` | `v1.31.0` | K8s version |
| `KYP_DRIVER` | `docker` | minikube driver |
| `KYP_NODE_CPUS` | `4` | per-node CPUs |
| `KYP_NODE_MEMORY_MB` | `6144` | per-node memory (MiB) |
| `KYP_NODE_DISK_GB` | `40` | per-node disk (GB) |

## How the Helm chart consumes the labels

Templates in `infra/charts/kyper-ds-platform/templates/` will set:

```yaml
# platform services (MLflow, CNPG Cluster, KubeRay operator)
nodeSelector:
  kyper.ai/role: platform

# workload namespace pods (RayJobs, dev-pods) — via RayCluster template
nodeSelector:
  kyper.ai/role: workload
```

This keeps long-lived services off the workload node, and ephemeral experiment jobs off the platform nodes.

## Optional: hard isolation via taints

The workload taint is **off by default** in `bootstrap.sh` so `kubectl run` debugging pods aren't blocked. Uncomment the taint block in `bootstrap.sh` to enforce:

```bash
kubectl taint node <workload-node> kyper.ai/role=workload:NoSchedule
```

Once tainted, workload pods must tolerate the taint. The chart's `rayjob.yaml.j2` template adds that toleration when `values.workload.taintEnabled=true`.

## Troubleshooting

- **"Node not Ready after bootstrap"** — CNI sometimes takes ~60s on first boot. Re-run `./scripts/status.sh` after a minute.
- **"addon enable failed"** — minikube tunnel / ingress-nginx sometimes races with node readiness. Re-run `bootstrap.sh`; it's idempotent.
- **"out of memory on docker driver"** — reduce `KYP_NODE_MEMORY_MB` or drop a node by editing `TOTAL_NODES` in `common.sh` (also drop from `PLATFORM_NODES`/`WORKLOAD_NODES`).
