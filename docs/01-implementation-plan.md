# 01 — Implementation Plan

Phased build order. Each phase ends in a demonstrable acceptance gate that exercises everything built so far. Do not start phase N+1 until phase N's gate is green.

## Phase 0 — Repo scaffolding (½ day)

**Deliverables**
- `kyper-framework/` tree (this repo) with `infra/`, `flow/`, `docs/`.
- Top-level `README.md`, `CONTRIBUTING.md`, `LICENSE` (internal), `.editorconfig`.
- Pre-commit hooks (ruff, yamllint, helm-lint).
- CI workflow stubs in `.github/workflows/` for `infra-lint.yaml` and `flow-ci.yaml`.

**Acceptance**
- `pre-commit run --all-files` green on an empty repo.
- CI runs on a dummy PR.

## Phase 1 — Platform Helm chart, minikube only (2 days)

**Deliverables** in `infra/charts/kyper-ds-platform/`
- `Chart.yaml` with deps: `cnpg`, `kuberay-operator`, `mlflow` (community chart).
- `values.yaml` + `values-minikube.yaml`.
- Templates: namespaces (`ds-platform`, `ds-workloads`), ResourceQuota, RBAC, CNPG `Cluster` for MLflow backend, MLflow secret/deployment/service/ingress, shared RWX PVC (minikube: CephFS).
- `NOTES.txt` with post-install `minikube tunnel` + port-forward instructions.
- `infra/README.md` with install runbook.

**Acceptance**
1. `helm dep update && helm upgrade --install ds-platform . -f values-minikube.yaml` completes with no errors.
2. CNPG operator + MLflow Postgres Cluster reach `Ready`.
3. MLflow UI reachable at `http://mlflow.minikube.local` (via `minikube tunnel`).
4. `MLFLOW_TRACKING_URI=... python -c "import mlflow; mlflow.set_experiment('smoke'); mlflow.log_metric('x', 1)"` lands in UI.
5. KubeRay operator running in `ds-platform`, watching cluster-wide.

## Phase 2 — `kyp` framework v0.1, local only (2 days)

**Deliverables** in `flow/kyp/`
- `pyproject.toml` — installable (`pip install -e .`), pinned deps.
- `kyp/task.py` — `@task`, `Task`/`Result` base dataclasses, pickle-check.
- `kyp/stage.py` — `@stage` with `name`, `ray_resources`, `worker_replicas`, `image_tag`.
- `kyp/pipeline.py` — `@pipeline` returning stage list.
- `kyp/executor.py` — `LocalExecutor` (sequential), `PoolExecutor` (mp).
- `kyp/mlflow.py` — parent/child/grandchild wiring, git SHA tagging, artifact logging.
- `kyp/platform.py` — profile loader, env propagation.
- `kyp/cli.py` — `kyp task run`, `kyp stage run`, `kyp pipeline run` (local backend only).
- `flow/kyp/README.md` with quickstart.

**Acceptance**
- `pytest tests/` green on the framework.
- A hand-written toy pipeline (2 stages, 3 tasks each) runs end-to-end locally and produces the expected MLflow tree: 1 pipeline run + 2 stage runs + 6 task runs.

## Phase 3 — Cookiecutter template v0.1 (1 day)

**Deliverables** in `flow/template/`
- Full cookiecutter tree (see [`03-flow-framework.md`](03-flow-framework.md)).
- Post-gen hook that runs `pip install -e .` and `pre-commit install`.
- Three starter stages: `features.py`, `detect.py`, `aggregate.py` (with placeholder logic).
- `configs/platforms/{local,minikube,gcp-dev}.yaml`.
- Notebooks 00/01/02 pre-wired.
- CI workflow running the parity test.

**Acceptance**
- `cookiecutter flow/template -o /tmp --no-input` produces a clean project.
- `cd /tmp/example-project && pip install -e . && kyp pipeline run --profile=local` runs three stages to completion.
- The parity test `pytest tests/test_pipeline_parity.py` is present and passes (local vs local-with-reloaded-config).

## Phase 4 — Ray execution in framework (2–3 days)

**Deliverables**
- `kyp/executor.py` — `RayExecutor(address=...)`.
- `kyp/ray_submit.py` — renders `RayJob` from Jinja template in `flow/kyp/templates/rayjob.yaml.j2`; submits via `kubectl apply`; streams logs; polls status.
- `kyp/cli.py` — `kyp pipeline run --profile=minikube` path: sequential RayJob submissions.
- `flow/kyp/templates/rayjob.yaml.j2` — resource-sized from `@stage(ray_resources=...)`, `runtime_env.working_dir` zipped from project.
- Dockerfile for `ds-runtime` base image.

**Acceptance**
1. A single-stage pipeline (`features` only) submits as a RayJob to minikube's `ds-workloads`, runs, tears down.
2. Pipeline run + stage run + all task runs appear in MLflow under the configured experiment.
3. Parity test extended: `pytest tests/test_pipeline_parity.py --backend=ray` produces byte-identical output to `--backend=local` on a fixture.
4. Failed task → stage marked failed → pipeline marked failed → `kyp pipeline run` exits non-zero.

## Phase 5 — First real port: oxy anomaly-detection slice (2 days)

**Deliverables**
- A new project generated from the template, named `oxy-anomaly-detection`.
- Three stages ported from `oxy/modules/anomaly-detection/`:
  - `features` stage wrapping `FeatureFactory`.
  - `detect` stage wrapping the 6-model loop.
  - `aggregate` stage wrapping `cross_sensor.run_cross_sensor` + `summary.json`.
- Pipeline config for SKAB subset.

**Acceptance**
- Runs end-to-end on minikube. Output matches `python run_all.py` from `oxy/main` on the same inputs (byte-diff on results CSVs).
- MLflow shows the full tree: pipeline → 3 stages → N tasks each.
- Retrying just `aggregate` works without re-running upstream stages.

## Phase 6 — Sweeps via Ray Tune + Optuna (2 days)

**Deliverables**
- `kyp sweep configs/sweeps/<file>.yaml` command.
- `kyp/sweep.py` — wraps Ray Tune with `OptunaSearch`, uses `MLflowLoggerCallback` for per-trial logging.
- Sweep config schema: search space, metric to optimize, `num_samples`, concurrency.
- One example sweep config in template (`configs/sweeps/iforest_hp.yaml`).

**Acceptance**
- `kyp sweep configs/sweeps/iforest_hp.yaml --profile=minikube` runs 20 trials.
- MLflow shows a parent sweep run + 20 child trial runs with params/metrics.
- Optuna best-trial selection logged as a parent tag.

## Phase 7 — GCP overlay (2 days)

**Deliverables** in `infra/charts/kyper-ds-platform/`
- `values-gcp-dev.yaml` — Cloud SQL connection string, GCS artifact root, GCE ingress, IAP, Workload Identity SA annotations.
- Cloud SQL provisioning via Terraform module (outside this repo, referenced in docs).
- Artifact Registry image push CI.
- `values-gcp-prod.yaml` (copy of dev with prod SA/bucket/sizing).

**Acceptance**
- Same chart installs cleanly on a GKE cluster with `-f values-gcp-dev.yaml`.
- A project generated from template with `--profile=gcp-dev` runs the same pipeline, writes to `gs://`, logs to GCP MLflow.
- Parity test extended: oxy-anomaly-detection produces byte-identical output on GKE vs minikube.

## Phase 8 — Hardening (ongoing)

- MLflow authentication (OIDC on GCP, basic auth on minikube).
- Observability: Prometheus metrics from MLflow + RayJobs.
- Artifact retention / cleanup CronJob.
- Model Registry integration when first DS requests it.
- DAG upgrade to Argo Workflows when first DS needs parallel stages.

## Summary table

| Phase | Duration | Gate |
|---|---|---|
| 0 — Scaffolding | ½ day | Pre-commit + CI green |
| 1 — Platform chart (minikube) | 2 days | MLflow smoke test from laptop |
| 2 — `kyp` v0.1 (local) | 2 days | Toy pipeline → correct MLflow tree |
| 3 — Cookiecutter template | 1 day | Generated project runs locally |
| 4 — Ray execution | 2–3 days | RayJob parity with local |
| 5 — Port oxy slice | 2 days | Byte-identical to legacy `run_all.py` |
| 6 — Sweeps | 2 days | 20-trial Optuna sweep visible in MLflow |
| 7 — GCP overlay | 2 days | Same pipeline runs on GKE |
| 8 — Hardening | ongoing | — |

**Total to MVP (through Phase 5):** ~10 working days.
**Total to GCP-ready (through Phase 7):** ~14 working days.
