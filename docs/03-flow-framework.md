# 03 — Flow / Framework

Everything in `flow/` is **Python + Jinja**. No Helm, no cluster YAML (beyond the RayJob Jinja template). DS team consumes this.

## Layout

```
flow/
├── README.md                                   overview + quickstart
├── kyp/                                        framework package (pip installable)
│   ├── pyproject.toml
│   ├── README.md
│   ├── kyp/
│   │   ├── __init__.py                         public API re-exports
│   │   ├── task.py                             @task, Task/Result base classes
│   │   ├── stage.py                            @stage
│   │   ├── pipeline.py                         @pipeline
│   │   ├── executor.py                         Local/Pool/Ray executors
│   │   ├── mlflow.py                           parent/child/grandchild wiring
│   │   ├── platform.py                         profile loader, env propagation
│   │   ├── ray_submit.py                       RayJob renderer + submit + poll
│   │   ├── sweep.py                            Ray Tune + Optuna wrapper
│   │   ├── cli.py                              `kyp` entrypoint (typer)
│   │   └── templates/
│   │       └── rayjob.yaml.j2
│   └── tests/
│       ├── test_task.py
│       ├── test_stage.py
│       ├── test_executor_local.py
│       ├── test_executor_ray.py
│       └── test_mlflow_tree.py
└── template/                                   cookiecutter project template
    ├── cookiecutter.json
    ├── hooks/
    │   └── post_gen_project.py
    └── {{cookiecutter.project_slug}}/
        ├── pyproject.toml
        ├── README.md
        ├── .kyp/project.yaml
        ├── configs/
        │   ├── platforms/
        │   │   ├── local.yaml
        │   │   ├── minikube.yaml
        │   │   └── gcp-dev.yaml
        │   ├── pipelines/default.yaml
        │   └── sweeps/iforest_hp.yaml
        ├── src/{{cookiecutter.project_slug}}/
        │   ├── __init__.py
        │   ├── stages/
        │   │   ├── features.py
        │   │   ├── detect.py
        │   │   └── aggregate.py
        │   ├── tasks.py
        │   └── types.py
        ├── pipelines/default.py
        ├── notebooks/
        │   ├── 00_explore.ipynb
        │   ├── 01_one_stage_local.ipynb
        │   └── 02_submit_pipeline.ipynb
        ├── tests/
        │   ├── test_tasks.py
        │   └── test_pipeline_parity.py
        ├── .github/workflows/ci.yaml
        └── .pre-commit-config.yaml
```

## Public API — `kyp.__init__`

Re-exports so the template imports from a single namespace:

```python
from kyp.task     import task, Task, Result
from kyp.stage    import stage
from kyp.pipeline import pipeline
from kyp.executor import LocalExecutor, PoolExecutor, RayExecutor

__all__ = ["task", "stage", "pipeline",
           "Task", "Result",
           "LocalExecutor", "PoolExecutor", "RayExecutor"]
```

## The three decorators

### `@task`

Wraps a pure function. Enforces input/output are dataclasses, registers for CLI discovery, handles per-task MLflow grandchild run.

```python
@task
def detect(t: DetectTask) -> DetectResult:
    ...
```

At call time (via executor), `kyp` wraps the invocation in an MLflow nested run, logs `t` as params, logs `DetectResult.metrics` as metrics, uploads `DetectResult.artifact_uris` as artifacts, tags status. DS code is unchanged.

### `@stage`

Declares a stage: its name, its Ray resource shape, its worker scaling bounds, its image tag, and the function that *builds* tasks from pipeline config.

```python
@stage(
    name            = "detect",
    ray_resources   = {"num_cpus": 4, "memory_gb": 8},
    worker_replicas = (0, 20),              # autoscale min..max
    image_tag       = "0.3.0",
)
def run(cfg: PipelineConfig) -> StageSpec:
    tasks = [DetectTask(sensor=s, model=m, hp=hp)
             for s in list_features(cfg.features_uri)
             for m, hp in cfg.active_models]
    return StageSpec(tasks=tasks, fn=detect, reduce=None)
```

The stage's `run(cfg)` is pure — it does not execute tasks. Executing is the executor's job. This separation is what lets local-sequential and ray-cluster runs share the same stage definition.

### `@pipeline`

A pipeline is a **list of stages** in sequential order. Not yet a DAG — that's a conscious deferral.

```python
@pipeline(name="rcsd_default")
def build(cfg: PipelineConfig) -> list:
    return [features.run, detect.run, aggregate.run]
```

## Executors

Strategy objects with a single method:

```python
class Executor(Protocol):
    name: str
    def map(self, fn: Callable[[T], R], items: list[T]) -> list[R]: ...
```

- **LocalExecutor** — sequential `for` loop. Default. Used in notebooks, CI, quick iteration.
- **PoolExecutor** — `concurrent.futures.ProcessPoolExecutor`. Single-machine speedup.
- **RayExecutor** — `ray.init(address=...)` + `ray.remote(**resources).remote(...)`. Works in two modes:
  - **In-cluster**: driver is the RayJob head, workers are other pods in the same RayJob.
  - **Client mode**: from laptop/dev-pod connecting to a long-lived RayCluster via `ray://`.

Executor selection happens in `kyp.cli` based on the platform profile. Never in DS code.

## MLflow wiring

Three nested levels, all emitted automatically:

```
pipeline run (parent)
  ├── stage run (child)
  │     ├── task run (grandchild)
  │     ├── task run (grandchild)
  │     └── ...
  ├── stage run (child)
  │     └── ...
```

Tags set automatically:

| Level | Tags |
|---|---|
| Pipeline | `kyp.pipeline`, `kyp.profile`, `kyp.backend`, `git.sha`, `git.dirty`, `kyp.user`, `kyp.version` |
| Stage | `kyp.stage`, `kyp.ray_resources`, `kyp.image_tag` |
| Task | `kyp.task_type`, `kyp.task_id` (deterministic hash), plus tags declared via `Task.mlflow_tags` |

Params: task dataclass fields auto-flattened (nested dicts → dotted keys).
Metrics: `Result.metrics` auto-logged.
Artifacts: each entry in `Result.artifact_uris` uploaded under its key.

DS never calls `mlflow.*`. Escape hatch: `kyp.mlflow.current()` returns the active MLflow client for custom logging inside a task.

## Platform profiles

Profile = environment routing config. Schema:

```yaml
# configs/platforms/minikube.yaml
name: minikube

mlflow:
  tracking_uri:    http://mlflow.ds-platform.svc.cluster.local:5000
  experiment_name: ${project_slug}           # resolved from .kyp/project.yaml

ray:
  mode:        raycluster                    # raycluster | rayjob | local
  address:     "auto"                        # used in client mode
  namespace:   ds-workloads
  image:       registry.local/kyper/ds-runtime:0.3.0
  image_pull_secrets: []

data:
  root: file:///data                         # CephFS mount on minikube

defaults:
  backend: local                             # kyp run default backend if not overridden
```

Profile is loaded at CLI entry. Export to env:
- `MLFLOW_TRACKING_URI`
- `MLFLOW_EXPERIMENT_NAME`
- `KYP_DATA_ROOT`
- `RAY_ADDRESS`
- `KYP_RUNTIME_IMAGE`

Tasks read `KYP_DATA_ROOT` to compute absolute URIs. No hardcoded paths anywhere.

## CLI surface

```
kyp task     run <TASK>   --in <path-or-stdin>
kyp stage    run <STAGE>  --config <yaml>
kyp pipeline run          --config configs/pipelines/default.yaml [--profile=X] [--backend=Y]
kyp sweep    run          --config configs/sweeps/iforest_hp.yaml [--profile=X]
kyp dev-pod  start                                               [--profile=X]
kyp status   cluster
kyp logs     <run-id>
```

`kyp pipeline run` on `--profile=minikube` or `--profile=gcp-dev`:
1. Start MLflow parent run.
2. For each stage:
   - Render RayJob manifest from `templates/rayjob.yaml.j2`.
   - `kubectl apply -n <ns>` the manifest.
   - Stream logs from head pod.
   - Poll `.status.jobStatus`.
   - On success → log stage metrics, next stage.
   - On failure → log traceback artifact, tag parent failed, exit 1.

On `--profile=local` or `--backend=local`: same flow, but `RayJob` is replaced by an in-process stage execution using `LocalExecutor`.

## Cookiecutter template — the starter files

### `src/{slug}/types.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class FeatureTask:
    in_uri:  str
    out_uri: str
    window_sizes: tuple[int, ...]

@dataclass(frozen=True)
class FeatureResult:
    metrics:       dict
    artifact_uris: dict
```

### `src/{slug}/stages/features.py`

```python
from kyp import task, stage
from {slug}.types import FeatureTask, FeatureResult
from {slug}.tasks import build_features_fn

build_features = task(build_features_fn)

@stage(name="features", ray_resources={"num_cpus": 2, "memory_gb": 4},
       worker_replicas=(0, 20), image_tag="{{cookiecutter.image_tag}}")
def run(cfg):
    tasks = [FeatureTask(in_uri=..., out_uri=..., window_sizes=cfg.windows)
             for ... in ...]
    return {"tasks": tasks, "fn": build_features, "reduce": None}
```

### `pipelines/default.py`

```python
from kyp import pipeline
from {slug}.stages import features, detect, aggregate

@pipeline(name="default")
def build(cfg):
    return [features.run, detect.run, aggregate.run]
```

### `configs/pipelines/default.yaml`

```yaml
pipeline: default
profile:  ${KYP_PROFILE:-local}

input_uri:     ${KYP_DATA_ROOT}/raw/rcsd-1yd/
features_uri:  ${KYP_DATA_ROOT}/features/rcsd-1yd/
results_uri:   ${KYP_DATA_ROOT}/results/rcsd-1yd/

features:
  windows: [5, 24, 50]

detect:
  active_models:
    - name: iforest
      hp: {n_estimators: 200, contamination: 0.05}
    - name: copod
      hp: {}
    # ...

aggregate:
  cross_sensor_threshold: 2
```

## Testing strategy

Two tiers:

1. **Framework tests** in `flow/kyp/tests/` — unit-test decorators, executors, MLflow wiring against a fixture MLflow server (sqlite).
2. **Template parity test** in each generated project — runs a 2-sensor, 1-model pipeline on `LocalExecutor` and `RayExecutor` (with `ray.init()` on laptop, no cluster), asserts identical output CSVs.

CI runs tier 1 on framework changes; tier 2 on template changes.

## Distribution

- **`kyp` package** published to a Kyper-internal Artifact Registry PyPI (`pip install kyp --index-url https://...`).
- **Template** hosted at `github.com/kyper/kyper-ds-template` (or internal GitLab). Pinned by SHA in `cookiecutter --checkout <sha>` calls for reproducibility.
- **Image** `kyper/ds-runtime` published to Artifact Registry on each `kyp` release, tagged by `kyp` version.
