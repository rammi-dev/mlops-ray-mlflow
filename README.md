# kyper-framework

Two-part foundation for data-science work at Kyper:

- **[`infra/`](infra/)** — platform deployed **once per cluster** via Helm. Provides MLflow, KubeRay operator, CloudNativePG-managed Postgres, namespaces, RBAC, and shared storage. Runs on minikube today, GCP tomorrow, same chart.
- **[`flow/`](flow/)** — the `kyp` Python framework + cookiecutter template used **per DS project**. Encodes the stage-per-RayJob execution pattern, local↔Ray parity, and MLflow wiring so DS writes only pure task functions.

Start with the design docs:

| Doc | Purpose |
|---|---|
| [`docs/00-architecture.md`](docs/00-architecture.md) | High-level architecture and the two-part split |
| [`docs/01-implementation-plan.md`](docs/01-implementation-plan.md) | Phased build order, milestones, acceptance gates |
| [`docs/02-infra-platform.md`](docs/02-infra-platform.md) | Helm chart structure, CNPG, MLflow, KubeRay, env overlays |
| [`docs/03-flow-framework.md`](docs/03-flow-framework.md) | `kyp` package, cookiecutter template, CLI surface |
| [`docs/04-pipeline-execution.md`](docs/04-pipeline-execution.md) | Stage-per-RayJob pattern, MLflow run tree, local vs cluster |
| [`docs/05-environments.md`](docs/05-environments.md) | Minikube → GCP portability matrix and the config-only delta |

## Principles

1. **Pure Python tasks.** DS writes `@task def fn(spec) -> result`. No Ray/MLflow imports in project code.
2. **Stage = RayJob.** Each pipeline stage runs as its own ephemeral Ray Job; no long-lived driver loops across stages.
3. **One codebase, many targets.** `--profile=local|minikube|gcp-dev|gcp-prod` switches execution substrate, not logic.
4. **Platform is Helm. Project is Cookiecutter.** Two orthogonal delivery mechanisms, versioned separately.
5. **MLflow is implicit.** Parent/child/grandchild run tree is emitted by the framework; DS never calls `mlflow.*`.
