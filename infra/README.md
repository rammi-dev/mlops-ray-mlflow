# infra/

Platform deployment — **Helm only**, no Python, no Kustomize.

Deployed **once per cluster** by the ops team. Provides the long-lived services that every DS project consumes:

- **CloudNativePG (CNPG) operator** — CNCF Postgres operator, manages the MLflow backend DB via a `Cluster` CR (backups, replicas, PITR, auto-failover).
- **MLflow tracking server** — runs with `--serve-artifacts` so Ray workers need no object-store credentials.
- **KubeRay operator** — watches `RayCluster` and `RayJob` CRs cluster-wide; workload jobs land in `ds-workloads`.
- **Namespaces, RBAC, ResourceQuota** — `ds-platform` (this chart's home) and `ds-workloads` (ephemeral job target).
- **Shared RWX PVC** — minikube only; on GCP, data lives in GCS.

See [`../docs/02-infra-platform.md`](../docs/02-infra-platform.md) for the full chart structure, CNPG rationale, values matrix, and install runbook.

## Planned tree

```
infra/
└── charts/
    └── kyper-ds-platform/
        ├── Chart.yaml
        ├── Chart.lock
        ├── templates/
        │   ├── namespaces.yaml
        │   ├── resourcequota.yaml
        │   ├── rbac.yaml
        │   ├── data-pvc.yaml
        │   ├── cnpg-cluster.yaml
        │   ├── mlflow-secret.yaml
        │   ├── mlflow-deployment.yaml
        │   ├── mlflow-service.yaml
        │   ├── mlflow-ingress.yaml
        │   └── NOTES.txt
        ├── values.yaml
        ├── values-minikube.yaml
        ├── values-gcp-dev.yaml
        └── values-gcp-prod.yaml
```

## Environments — the only things that differ

| Dimension | minikube | gcp-dev |
|---|---|---|
| Image registry | `registry.local` | Artifact Registry |
| MLflow artifact store | Ceph RGW `s3://` | GCS `gs://` |
| Object-store creds | K8s Secret | Workload Identity |
| CNPG Postgres backup dest | Ceph RGW | GCS |
| CNPG instances | 1 | 2 |
| Ingress | nginx | GCE + IAP |
| Storage class | `rook-ceph-block` / `rook-cephfs` | `premium-rwo` / Filestore |
| Data PVC | enabled | disabled (GCS direct) |

See [`../docs/05-environments.md`](../docs/05-environments.md) for the full portability matrix.

## Install (once standed up)

```bash
# minikube
helm repo add cnpg            https://cloudnative-pg.github.io/charts
helm repo add kuberay         https://ray-project.github.io/kuberay-helm/
helm repo add community-charts https://community-charts.github.io/helm-charts
helm dep update charts/kyper-ds-platform

helm upgrade --install ds-platform charts/kyper-ds-platform \
  -f charts/kyper-ds-platform/values-minikube.yaml \
  --create-namespace -n ds-platform

# smoke
kubectl -n ds-platform get clusters,deployments,svc,ingress
minikube tunnel &
MLFLOW_TRACKING_URI=http://mlflow.minikube.local \
  python -c "import mlflow; mlflow.set_experiment('smoke'); mlflow.log_metric('x', 1)"
```

## Build order

1. Scaffold `Chart.yaml` + `values.yaml` + `values-minikube.yaml`.
2. Add namespace + quota + RBAC templates.
3. Add CNPG subchart dep and a `Cluster` CR for MLflow's DB.
4. Add MLflow secret/deployment/service/ingress templates wired to the CNPG app secret.
5. Add KubeRay operator subchart dep, confirm it watches cluster-wide.
6. Add data PVC template (minikube only).
7. First install + smoke test (Phase 1 gate in [`../docs/01-implementation-plan.md`](../docs/01-implementation-plan.md)).
8. Clone values to `values-gcp-dev.yaml`, patch the 10 deltas from the portability matrix.

Nothing is hand-edited per environment. One chart, N values files.
