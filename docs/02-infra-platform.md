# 02 — Infra / Platform

Everything in `infra/` is **YAML + Helm**. No Python. Ops / platform team owns this.

## Chart layout

```
infra/
├── README.md                                   install + upgrade runbook
└── charts/
    └── kyper-ds-platform/
        ├── Chart.yaml
        ├── Chart.lock
        ├── charts/                             fetched via `helm dep update`
        ├── templates/
        │   ├── _helpers.tpl                    naming, labels, common env
        │   ├── namespaces.yaml                 ds-platform + ds-workloads
        │   ├── resourcequota.yaml              quota on ds-workloads
        │   ├── limitrange.yaml                 default requests/limits
        │   ├── rbac.yaml                       DS group → edit on ds-workloads
        │   ├── data-pvc.yaml                   CephFS RWX PVC (toggle via values)
        │   ├── cnpg-cluster.yaml               Postgres Cluster CR for MLflow
        │   ├── mlflow-secret.yaml              DB URI + object-store creds
        │   ├── mlflow-deployment.yaml          tracking server, --serve-artifacts
        │   ├── mlflow-service.yaml
        │   ├── mlflow-ingress.yaml             toggle per env
        │   └── NOTES.txt
        ├── values.yaml                         sensible defaults (minikube-friendly)
        ├── values-minikube.yaml                explicit minikube overrides
        ├── values-gcp-dev.yaml                 Cloud SQL, GCS, WI, GCE ingress
        └── values-gcp-prod.yaml
```

## Subchart dependencies

Umbrella chart pulls community charts; we don't vendor or fork.

```yaml
# Chart.yaml
apiVersion: v2
name: kyper-ds-platform
version: 0.1.0
type: application

dependencies:
  - name: cloudnative-pg                      # CNPG operator (CNCF sandbox)
    version: "0.22.1"
    repository: https://cloudnative-pg.github.io/charts
    alias: cnpg
    condition: cnpg.enabled

  - name: kuberay-operator
    version: "1.2.2"
    repository: https://ray-project.github.io/kuberay-helm/
    condition: kuberayOperator.enabled

  - name: mlflow
    version: "0.7.19"
    repository: https://community-charts.github.io/helm-charts
    condition: mlflow.enabled
```

**Why CNPG:**

- CNCF-sandbox Postgres operator — the community default.
- Native support for replicas, streaming replication, backups to S3/GCS, point-in-time recovery, WAL archiving, automated failover.
- Declarative `Cluster` CR — the app just references it.
- Works identically on minikube and GCP (only backup destination changes).
- Strictly better than a Bitnami Postgres StatefulSet for any long-lived workload.

## CNPG Cluster for MLflow

Deployed by our chart *after* the CNPG operator is Ready. Example template (stub):

```yaml
# templates/cnpg-cluster.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: {{ include "kyper.name" . }}-mlflow-pg
  namespace: {{ .Values.global.namespace.platform }}
spec:
  instances: {{ .Values.mlflow.postgres.instances | default 1 }}       # 1 on minikube, 2+ on GCP
  imageName: ghcr.io/cloudnative-pg/postgresql:16.4
  storage:
    size: {{ .Values.mlflow.postgres.size | default "5Gi" }}
    storageClass: {{ .Values.mlflow.postgres.storageClass }}
  bootstrap:
    initdb:
      database: mlflow
      owner: mlflow
      secret:
        name: {{ include "kyper.name" . }}-mlflow-pg-app          # CNPG auto-creates
  backup:
    {{- if .Values.mlflow.postgres.backup.enabled }}
    barmanObjectStore:
      destinationPath: {{ .Values.mlflow.postgres.backup.destinationPath }}
      {{- if eq .Values.mlflow.postgres.backup.provider "s3" }}
      s3Credentials:
        accessKeyId:     { name: mlflow-pg-backup, key: AWS_ACCESS_KEY_ID }
        secretAccessKey: { name: mlflow-pg-backup, key: AWS_SECRET_ACCESS_KEY }
      endpointURL: {{ .Values.mlflow.postgres.backup.s3EndpointUrl }}
      {{- else if eq .Values.mlflow.postgres.backup.provider "gcs" }}
      googleCredentials:
        gkeEnvironment: true            # Workload Identity
      {{- end }}
    retentionPolicy: "30d"
    {{- end }}
```

- On minikube: 1 instance, backup destination `s3://mlflow-pg-backup/` (Ceph RGW).
- On GCP: 2 instances, backup destination `gs://kyper-mlflow-pg-backup/`, WI-bound GSA.

MLflow reads its DB URI from a Secret whose value is templated from the CNPG-generated app secret (`<cluster>-app` contains `uri`, `host`, `port`, `user`, `password`, `dbname`).

## MLflow configuration

Key decisions, encoded in the chart's MLflow values:

| Setting | Value | Why |
|---|---|---|
| `--serve-artifacts` | **on** | Workers never need object-store credentials. |
| `--artifacts-destination` | `s3://...` or `gs://...` | Per-env URI; MLflow proxies. |
| `--backend-store-uri` | `postgresql+psycopg2://...` | Points at CNPG-managed cluster. |
| Replicas | 1 (minikube) / 2+ (GCP) | Stateless server; scale horizontally. |
| Ingress | nginx (minikube) / GCE+IAP (GCP) | Per-env via values. |
| Default experiment | not set by chart | Project sets via `kyp` config. |

## Namespaces

Created by the chart (idempotent — `kubectl get ns` check in a helper):

| Namespace | Purpose | Lifetime |
|---|---|---|
| `cnpg-system` | CNPG operator | cluster lifetime |
| `ds-platform` | MLflow server, MLflow Postgres Cluster, KubeRay operator | cluster lifetime |
| `ds-workloads` | RayClusters, RayJobs, dev-pods, PVCs for data | project lifetime |

`ds-workloads` has a `ResourceQuota` sized per env (minikube: 40 CPU / 100 GiB; GCP: higher).

## Values matrix — the only things that differ

| Key | minikube | GCP |
|---|---|---|
| `global.image.registry` | `registry.local` | `us-central1-docker.pkg.dev/kyper-tech-490407/ds` |
| `global.data.root` | `file:///data` (CephFS PVC) | `gs://kyper-oxy-data` |
| `mlflow.artifactRoot` | `s3://mlflow-artifacts/` (Ceph RGW) | `gs://kyper-mlflow-artifacts/` |
| `mlflow.s3EndpointUrl` | `http://rook-ceph-rgw-...` | unset (native GCS) |
| `mlflow.postgres.instances` | 1 | 2 |
| `mlflow.postgres.storageClass` | `rook-ceph-block` | `premium-rwo` |
| `mlflow.postgres.backup.provider` | `s3` | `gcs` |
| `mlflow.ingress.className` | `nginx` | `gce` |
| `mlflow.ingress.annotations` | (minimal) | `networking.gke.io/managed-certificates`, IAP |
| `mlflow.serviceAccount.annotations` | (none) | `iam.gke.io/gcp-service-account` |
| `workload.dataPVC.enabled` | `true` | `false` |

Everything else — chart structure, templates, resource names, labels — is shared.

## Install commands

```bash
# minikube (first time)
minikube start --cpus 8 --memory 16g
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm repo add kuberay https://ray-project.github.io/kuberay-helm/
helm repo add community-charts https://community-charts.github.io/helm-charts
helm dep update infra/charts/kyper-ds-platform

helm upgrade --install ds-platform infra/charts/kyper-ds-platform \
  -f infra/charts/kyper-ds-platform/values-minikube.yaml \
  --create-namespace -n ds-platform

# get URL
kubectl -n ds-platform get ingress
minikube tunnel &

# smoke test
export MLFLOW_TRACKING_URI=http://mlflow.minikube.local
python -c "import mlflow; mlflow.set_experiment('smoke'); mlflow.log_metric('x', 1)"

# later, on GKE:
helm upgrade --install ds-platform infra/charts/kyper-ds-platform \
  -f infra/charts/kyper-ds-platform/values-gcp-dev.yaml \
  --create-namespace -n ds-platform
```

## Upgrade semantics

- **CNPG operator upgrade** — handled by the subchart; follow CNPG's major-version upgrade notes.
- **Postgres minor upgrade** — change `imageName` on the `Cluster` CR; CNPG rolls replicas in-place.
- **MLflow upgrade** — bump community chart version in `Chart.yaml`; `helm dep update`; `helm upgrade`.
- **KubeRay operator upgrade** — bump subchart version. RayJobs in flight complete on the old operator.

All upgrades are `helm diff upgrade` → review → `helm upgrade`. No hand-editing.

## Not in the chart (deliberately)

- **Dev pods** — created on-demand by DS via `kyp dev-pod` (template side).
- **Per-project secrets** — DS responsibility; stored via ExternalSecrets + Secret Manager on GCP.
- **Monitoring (Prometheus/Grafana)** — separate chart, not our concern.
- **Image builds** — CI responsibility.
- **RayClusters** — ephemeral, created by `kyp pipeline run` via RayJob. No long-lived RayCluster in the chart.
