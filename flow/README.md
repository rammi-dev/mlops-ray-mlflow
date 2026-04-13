# flow/

DS-facing framework — **Python + Jinja only**, no Helm.

Two artifacts, versioned independently:

- **`kyp/`** — framework package. `pip install kyp`. Provides `@task`, `@stage`, `@pipeline`, executors (local / pool / ray), MLflow wiring, RayJob renderer, and the `kyp` CLI.
- **`template/`** — cookiecutter template. `cookiecutter gh:kyper/kyper-ds-template`. Produces a project scaffold with stages, notebooks, pipeline configs, tests, and CI.

See [`../docs/03-flow-framework.md`](../docs/03-flow-framework.md) for the full design, public API, and cookiecutter layout; [`../docs/04-pipeline-execution.md`](../docs/04-pipeline-execution.md) for the stage-per-RayJob execution model and MLflow run tree.

## Planned tree

```
flow/
├── kyp/                            # the framework package
│   ├── pyproject.toml
│   ├── kyp/
│   │   ├── __init__.py             # public API re-exports
│   │   ├── task.py                 # @task + Task/Result
│   │   ├── stage.py                # @stage
│   │   ├── pipeline.py             # @pipeline
│   │   ├── executor.py             # LocalExecutor, PoolExecutor, RayExecutor
│   │   ├── mlflow.py               # parent/child/grandchild wiring
│   │   ├── platform.py             # profile loader + env propagation
│   │   ├── ray_submit.py           # RayJob render + submit + poll
│   │   ├── sweep.py                # Ray Tune + Optuna wrapper
│   │   ├── cli.py                  # `kyp` entry point (typer)
│   │   └── templates/
│   │       └── rayjob.yaml.j2
│   └── tests/
│
└── template/                       # cookiecutter project template
    ├── cookiecutter.json
    ├── hooks/
    │   └── post_gen_project.py
    └── {{cookiecutter.project_slug}}/
        ├── pyproject.toml
        ├── src/{{cookiecutter.project_slug}}/
        │   ├── stages/            # features.py, detect.py, aggregate.py
        │   ├── tasks.py
        │   └── types.py
        ├── pipelines/default.py
        ├── configs/
        │   ├── platforms/          # local.yaml, minikube.yaml, gcp-dev.yaml
        │   ├── pipelines/
        │   └── sweeps/
        ├── notebooks/              # 00_explore, 01_one_stage_local, 02_submit_pipeline
        ├── tests/                  # including test_pipeline_parity.py
        └── .github/workflows/ci.yaml
```

## The three contracts a DS fills in

1. **`@task` functions** — pure, typed, pickleable.
2. **`@stage` factories** — `(cfg) -> {tasks, fn, reduce}`.
3. **`@pipeline` list** — ordered stages.

Everything else — MLflow logging, Ray submission, K8s RayJob rendering, profile switching — is inherited.

## CLI

```
kyp pipeline run  --config configs/pipelines/default.yaml  [--profile=X] [--backend=Y]
kyp stage    run  <STAGE>  --config configs/pipelines/default.yaml
kyp task     run  <TASK>   --in <json-or-file>
kyp sweep    run  --config configs/sweeps/iforest_hp.yaml
kyp dev-pod  start
kyp status   cluster
kyp logs     <run-id>
```

## Backends

| Backend | When | Mechanism |
|---|---|---|
| `local` | laptop, notebooks, CI | sequential `for` loop |
| `pool` | single-machine speedup | `ProcessPoolExecutor` |
| `ray`  | minikube / GKE | one ephemeral RayJob per stage |

Picked automatically from the platform profile; overridable with `--backend=`.

## Build order

See [`../docs/01-implementation-plan.md`](../docs/01-implementation-plan.md) Phases 2–4.

Framework first (`kyp/`, local backend). Then cookiecutter template consuming it. Then Ray execution. The parity test (LocalExecutor output == RayExecutor output) is the gate before anything ships.
