# 05 — Environments

Portability matrix. "Minikube now, GCP later" is a **values-file change + image registry swap**, not a rewrite.

## Targets

| Env | Purpose | When ready |
|---|---|---|
| `local` | Laptop dev, no K8s. LocalExecutor + MLflow at `localhost:5000`. | Phase 2 |
| `minikube` | On-prem dev cluster. Full stack: CNPG, MLflow, KubeRay. | Phase 1 |
| `gcp-dev` | GKE dev. Cloud SQL via CNPG or managed. GCS artifacts. Workload Identity. | Phase 7 |
| `gcp-prod` | GKE prod. Higher replicas, restricted RBAC, IAP. | After gcp-dev |

## What differs between environments

Only these, per the Helm values files:

| Dimension | minikube | gcp-dev |
|---|---|---|
| **Image registry** | `registry.local` | `us-central1-docker.pkg.dev/kyper-tech-490407/ds` |
| **MLflow backend** | CNPG Cluster in-cluster | CNPG Cluster in-cluster *or* Cloud SQL |
| **MLflow backend backup** | Ceph RGW `s3://mlflow-pg-backup/` | GCS `gs://kyper-mlflow-pg-backup/` |
| **MLflow artifact store** | Ceph RGW `s3://mlflow-artifacts/` | GCS `gs://kyper-mlflow-artifacts/` |
| **MLflow object-store creds** | K8s Secret (Ceph S3 keys) | Workload Identity (no secrets) |
| **Data root** | `file:///data` (CephFS PVC) | `gs://kyper-oxy-data` (direct) |
| **Ingress class** | `nginx` | `gce` with IAP backend-config |
| **DNS** | `*.minikube.local` via `/etc/hosts` | real DNS `*.ds.kyper.internal` |
| **TLS** | self-signed or off | GCE-managed certs |
| **Storage classes** | `rook-ceph-block`, `rook-cephfs` | `premium-rwo`, Filestore (if needed) |
| **ResourceQuota on ds-workloads** | 40 CPU / 100 GiB | larger, per-team |

**Everything else is identical.** Same chart templates, same RayJob template, same `kyp` code, same project code.

## Image strategy

One runtime image, `ds-runtime`, pinned to a `kyp` release version. Two registries, built once by CI:

```
ds-runtime:0.3.0
├── registry.local/kyper/ds-runtime:0.3.0                              (minikube)
└── us-central1-docker.pkg.dev/kyper-tech-490407/ds/ds-runtime:0.3.0   (GCP)
```

Profile picks the right one. Never rebuild just to change environment.

Image contents (stable across projects):

- Python 3.11
- `ray[default,tune]` pinned
- `mlflow[extras]` pinned
- `fsspec`, `s3fs`, `gcsfs` (both — same image used on both clouds)
- Core DS deps: `pandas`, `numpy`, `scipy`, `scikit-learn`, `pyod`, `xgboost`, `torch`, `statsmodels`, `optuna`
- `kyp` framework

Per-project extras via `runtime_env.pip` (installed once per worker, no image rebuild).

## Credentials handling

**minikube** — K8s Secret pattern:
- Chart creates Secrets with Ceph S3 keys, Postgres password, etc.
- Pods mount them as env vars.
- DS never sees them.

**GCP** — Workload Identity pattern (strongly preferred):
- Each service account in K8s (KSA) is bound to a GCP service account (GSA) via IAM.
- Pods authenticate as the GSA automatically — no keys mounted, no secrets in the cluster.
- `mlflow-server` KSA → `mlflow-sa@kyper-tech-490407` GSA with `roles/storage.objectAdmin` on `kyper-mlflow-artifacts`.
- RayJobs use a `ray-worker` KSA → `ray-worker-sa@...` with `roles/storage.objectUser` on data buckets.

Set via chart values:

```yaml
# values-gcp-dev.yaml
mlflow:
  serviceAccount:
    create: true
    annotations:
      iam.gke.io/gcp-service-account: mlflow-sa@kyper-tech-490407.iam.gserviceaccount.com

workload:
  rayServiceAccount:
    create: true
    annotations:
      iam.gke.io/gcp-service-account: ray-worker-sa@kyper-tech-490407.iam.gserviceaccount.com
```

## Data access

- **minikube**: data is on a shared CephFS PVC mounted at `/data` inside RayClusters. Path is `/data/raw/rcsd-1yd/...`. Tasks use `file:///data/...` URIs.
- **GCP**: data is in GCS. No PVC mount. Tasks use `gs://kyper-oxy-data/raw/rcsd-1yd/...`. `gcsfs` handles reads/writes transparently.

**Same task code.** `fsspec` dispatches on the URI scheme. Cache behavior can be tuned per env if needed but isn't a code change.

## Upgrade / promotion flow

1. A project is developed against `--profile=local` (laptop).
2. DS validates on `--profile=minikube` — full infra, small data.
3. Ops promotes the project to `--profile=gcp-dev` — full-scale data.
4. After validation, same project tagged and run on `--profile=gcp-prod` for scheduled runs.

Same project repo, same code, same `pipelines/default.py`. Only the profile flag and dataset URIs change.

## What's NOT portable, and why it's fine

- **Ingress annotations** — GCE IAP config has no minikube equivalent. Lives in the values file, not project code.
- **Backup schedules** — CNPG accepts the same schema on both; destination differs. Values file.
- **Storage class names** — hardcoded per env. Values file.
- **DNS** — no attempt to unify. Values file + `/etc/hosts` on minikube.

All differences live in **Helm values**, never in chart templates (so they're not hand-edited per env), never in Python (so DS code doesn't know).

## Cost / sizing considerations (GCP only)

| Component | Sizing |
|---|---|
| MLflow server | 2 replicas, 500m/1Gi requests. Stateless; scale horizontally if UI slows. |
| CNPG MLflow DB | 2 instances (primary + replica), `db-custom-2-8192` tier in Cloud SQL or `pd-ssd` if self-hosted. |
| KubeRay operator | 1 replica, 100m/256Mi. Low footprint. |
| RayJob head | 1 CPU / 2 GiB per job. Short-lived. |
| RayJob workers | Per stage declaration. Use preemptible / spot nodes for fan-out workloads; fall back to regular for orchestrator. |
| Artifact store | GCS Nearline is cheap; lifecycle-rule 30-day → Coldline for old experiments. |

## Migration out of K8s (future option)

The stage-per-RayJob pattern was chosen partly so you can swap K8s for Vertex AI or Batch later:

- RayJob → Vertex AI Custom Job with Ray-on-Vertex image.
- Or RayJob → GCP Batch Job with `ray start --head` in the entrypoint.
- Or RayJob → Dataflow Flex Template (overkill, but possible).

The framework would swap `RayExecutor`'s submission backend from KubeRay to one of these, everything else stays the same. Not a Phase-7 concern; noted here so you don't paint yourself into a K8s-only corner accidentally.
