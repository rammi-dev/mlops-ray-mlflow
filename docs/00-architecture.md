# 00 — Architecture

## Two-part split

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          kyper-framework                                │
├────────────────────────────────────┬────────────────────────────────────┤
│  infra/   (deployed once / env)    │  flow/   (cloned per DS project)   │
│  ──────                            │  ─────                             │
│  Helm umbrella chart:              │  kyp framework package:            │
│   • CNPG operator (Postgres)       │   • @task / @stage / @pipeline     │
│   • Postgres Cluster (MLflow)      │   • LocalExecutor / RayExecutor    │
│   • MLflow tracking server         │   • MLflow parent/child wiring     │
│   • KubeRay operator               │   • RayJob renderer + submitter    │
│   • Namespaces (platform/workload) │   • `kyp` CLI                      │
│   • Shared PVC (minikube) / GCS    │                                    │
│   • RBAC + ResourceQuota           │  Cookiecutter template:            │
│                                    │   • src/{slug}/stages/             │
│  Values:                           │   • pipelines/default.py           │
│   • values-minikube.yaml           │   • configs/platforms/*.yaml       │
│   • values-gcp-dev.yaml            │   • notebooks/ (pre-wired)         │
│   • values-gcp-prod.yaml           │   • tests/ (parity gate)           │
└────────────────────────────────────┴────────────────────────────────────┘
           ↑                                          ↑
      helm install                              cookiecutter new
      (ops / platform team)                     (data scientist)
```

Infra and flow evolve on independent release trains. A DS project pins `kyp` by version in its `pyproject.toml`; a cluster pins the platform chart by version in its release. Upgrades are not coupled.

## Runtime topology (single cluster)

```
┌────────────────────────── cluster ──────────────────────────────────┐
│                                                                     │
│  ns: cnpg-system                                                    │
│   └── cnpg-operator            (watches Cluster CRs cluster-wide)   │
│                                                                     │
│  ns: ds-platform               (long-lived services)                │
│   ├── mlflow-pg (Cluster CR)   → managed by CNPG                    │
│   │     └── primary + replica pods + backup CronJob                 │
│   ├── mlflow-server            Deployment + Service + Ingress       │
│   │     serves UI + proxies artifacts (--serve-artifacts)           │
│   └── kuberay-operator         (watches RayCluster/RayJob CRs)      │
│                                                                     │
│  ns: ds-workloads              (ephemeral experiment jobs)          │
│   ├── RayJob/features-<run-id>       ← submitted by `kyp pipeline`  │
│   │     head + worker pods, autoscaled, torn down on success        │
│   ├── RayJob/detect-<run-id>                                        │
│   ├── RayJob/aggregate-<run-id>                                     │
│   ├── PVC data-rwx (minikube only — on GCP, use gs://)              │
│   └── dev-pod-<user>           (VSCode Remote-SSH targets)          │
│                                                                     │
│  Object storage (artifacts)                                         │
│    minikube: Ceph RGW  s3://mlflow-artifacts/                       │
│    GCP:      GCS       gs://kyper-mlflow-artifacts/                 │
└─────────────────────────────────────────────────────────────────────┘
```

## Execution flow (one pipeline run)

```
DS types:   kyp pipeline run configs/pipelines/default.yaml --profile=minikube

┌──────────────────────────────────────────────────────────────────────┐
│ 1. kyp loads profile, opens MLflow parent run, tags git_sha/user     │
│ 2. For each stage in pipeline (features → detect → aggregate):       │
│      a. Renders RayJob manifest from template                        │
│      b. kubectl apply -n ds-workloads                                │
│      c. Streams head-pod logs, polls .status.jobStatus               │
│      d. On SUCCESS → advance; on FAILURE → tag parent, abort         │
│ 3. Final: tag parent status=ok, print MLflow run URL                 │
└──────────────────────────────────────────────────────────────────────┘

Inside each RayJob:
  head runs `kyp stage-exec <stage_name> <cfg_uri> <parent_run_id>`
    → starts nested MLflow run for the stage
    → builds task list (fan-out) via stage's build_tasks()
    → ray.get([remote.remote(t) for t in tasks])
       each task = grandchild MLflow run with params/metrics/artifacts
    → optional reduce(results)
```

## Why this shape

- **Stage-per-RayJob** gives isolation, per-stage right-sizing, retry granularity, and maps directly to Vertex AI / Batch jobs when moving off K8s.
- **CNPG** handles Postgres lifecycle properly (backups, failover, WAL archiving) — much stronger than a Bitnami Postgres chart, and is the CNCF-blessed operator.
- **Helm umbrella** lets each piece (CNPG, KubeRay, MLflow) be pinned to upstream community charts instead of reinvented.
- **Cookiecutter** means the DS onboarding is `cookiecutter <url>` → working project. No framework assembly in every project.
- **Platform profile YAML** is the single point of environment routing. Python code is environment-agnostic.

## Out of scope (for now)

- **Multi-cluster MLflow federation** — one tracking server per environment, export history manually if needed.
- **Workflow engine (Argo/Airflow)** — `kyp pipeline run` submits stages sequentially. Upgrade to Argo only if parallel-stage DAGs become a real need.
- **Model Registry** — MLflow supports it; not wired into templates until a DS asks.
- **Feature store** — separate concern, separate project.
- **GPU scheduling & sharing** — add when the first GPU workload arrives.

## Repo conventions

- `infra/` is pure YAML + Helm — no Python.
- `flow/` is pure Python + Jinja (template) — no Helm.
- `docs/` holds design; per-component operational docs live next to the component (`infra/README.md`, `flow/kyp/README.md`).
- Versioning: `infra/` chart and `flow/kyp` package are tagged independently.
