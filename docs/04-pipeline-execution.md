# 04 — Pipeline Execution

How pipelines actually run, end-to-end. Covers the stage-per-RayJob pattern, the MLflow run tree, failure semantics, and what changes between `--backend=local` and `--backend=ray`.

## Invocation

```bash
kyp pipeline run configs/pipelines/default.yaml --profile=minikube
```

`kyp` then:

1. Loads `.kyp/project.yaml` for `project_slug`.
2. Loads `configs/platforms/minikube.yaml` as the active profile.
3. Loads the pipeline config YAML.
4. Imports `pipelines.default.build(cfg)` to get the ordered stage list.
5. Starts MLflow parent run, tags it.
6. Iterates stages. For each, either:
   - **`--backend=local`**: calls `stage(cfg)` in-process, runs via `LocalExecutor`.
   - **`--backend=ray`** or profile ≠ local: submits a `RayJob` to `ds-workloads`, streams logs, polls status.
7. On success → tags parent `status=ok`. On any stage failure → tags `status=failed`, exits 1.

## Stage = RayJob (cluster path)

Each stage launches **its own ephemeral RayJob**. No long-lived RayCluster across stages.

### Why ephemeral, per-stage

| Property | Benefit |
|---|---|
| Isolation | Features-stage OOM doesn't kill detect-stage. Fresh head pod each time. |
| Right-sizing | `features` asks for 2 CPU workers; `detect` for 4 CPU; `train` for GPUs. Different RayJobs, different specs. |
| Retry granularity | Rerun just `aggregate` — previous stages' outputs on disk/GCS unaffected. |
| Cost / autoscaling | Between stages, worker pods scale to zero. Nothing idle. |
| Observability | One RayJob CR per stage. `kubectl get rayjobs -n ds-workloads` shows the pipeline at a glance. |
| Cloud portability | Maps 1:1 to a Vertex AI Custom Job or GCP Batch Job when/if you move off K8s. |

### RayJob manifest (rendered per stage)

`kyp` renders a Jinja template at submit time:

```yaml
# flow/kyp/templates/rayjob.yaml.j2
apiVersion: ray.io/v1
kind: RayJob
metadata:
  name: {{ project_slug }}-{{ stage.name }}-{{ run_id }}
  namespace: {{ platform.ray.namespace }}
  labels:
    kyp.pipeline:   "{{ pipeline.name }}"
    kyp.stage:      "{{ stage.name }}"
    kyp.run-id:     "{{ run_id }}"
    kyp.mlflow-run: "{{ mlflow_parent_run_id }}"
spec:
  entrypoint: |
    kyp stage-exec {{ stage.name }} {{ stage_cfg_uri }} {{ mlflow_parent_run_id }}
  runtimeEnvYAML: |
    working_dir: {{ working_dir_uri }}    # zipped project uploaded to staging bucket
    env_vars:
      MLFLOW_TRACKING_URI:    "{{ platform.mlflow.tracking_uri }}"
      MLFLOW_EXPERIMENT_NAME: "{{ platform.mlflow.experiment_name }}"
      KYP_DATA_ROOT:          "{{ platform.data.root }}"
      KYP_PROFILE:            "{{ platform.name }}"
      KYP_PARENT_RUN_ID:      "{{ mlflow_parent_run_id }}"
    pip:
      {% for extra in stage.extra_pip %}
      - {{ extra }}
      {% endfor %}
  rayClusterSpec:
    rayVersion: "2.35.0"
    headGroupSpec:
      template:
        spec:
          serviceAccountName: {{ platform.ray.service_account }}
          containers:
            - name: ray-head
              image: {{ platform.ray.image }}
              resources:
                requests: { cpu: 1, memory: 2Gi }
    workerGroupSpecs:
      - groupName: default-workers
        replicas:    {{ stage.worker_replicas[0] }}
        minReplicas: {{ stage.worker_replicas[0] }}
        maxReplicas: {{ stage.worker_replicas[1] }}
        template:
          spec:
            containers:
              - name: ray-worker
                image: {{ platform.ray.image }}
                resources:
                  requests:
                    cpu:    {{ stage.ray_resources.num_cpus }}
                    memory: {{ stage.ray_resources.memory_gb }}Gi
                  {% if stage.ray_resources.num_gpus %}
                  limits:
                    nvidia.com/gpu: {{ stage.ray_resources.num_gpus }}
                  {% endif %}
  shutdownAfterJobFinishes: true
  ttlSecondsAfterFinished:  300
```

### What the RayJob runs

Entrypoint is `kyp stage-exec` — a framework-provided command that re-enters the project in-cluster:

```python
# kyp/cli.py (sketch)
@app.command("stage-exec")
def stage_exec(stage_name: str, cfg_uri: str, parent_run_id: str):
    cfg   = load_pipeline_config(cfg_uri)
    stage = lookup_stage_from_project(stage_name)     # imports {slug}.stages.<name>

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])

    with mlflow.start_run(run_id=parent_run_id):               # resume parent
        with mlflow.start_run(nested=True,
                              run_name=f"stage:{stage_name}") as stage_run:
            mlflow.set_tag("kyp.stage", stage_name)
            spec = stage(cfg)                                  # build_tasks()
            executor = RayExecutor(address="auto",
                                   resources=stage.ray_resources)
            results = executor.map(spec.fn, spec.tasks)
            mlflow.log_metric("n_tasks_ok",     sum(1 for r in results if r.ok))
            mlflow.log_metric("n_tasks_failed", sum(1 for r in results if not r.ok))
            if spec.reduce:
                spec.reduce(results, cfg)
```

## MLflow run tree

```
Experiment: {project_slug}
│
└── Run: pipeline:default @ 2026-04-13T14:22:01Z
     tags: kyp.profile=minikube, kyp.backend=ray, git.sha=abc1234
     params: config_snapshot (as artifact)
     │
     ├── Run: stage:features
     │    tags: kyp.stage=features, kyp.ray_resources={cpu:2,mem:4}
     │    metrics: n_tasks_ok=25, duration_s=41
     │    │
     │    ├── Run: build_features / sensor=75XI821BX.pv
     │    │    params: in_uri, out_uri, window_sizes
     │    │    metrics: rows=35036
     │    │    artifacts: out_uri
     │    ├── Run: build_features / sensor=75XI822BY.pv
     │    └── ... (23 more)
     │
     ├── Run: stage:detect
     │    metrics: n_tasks_ok=150, duration_s=210
     │    │
     │    ├── Run: detect / sensor=75XI821BX.pv, model=iforest
     │    │    params: contamination=0.05, n_estimators=200
     │    │    metrics: n_anomalies=42, score_mean=0.12
     │    │    artifacts: results_csv, diagnostic_png
     │    └── ... (149 more)
     │
     └── Run: stage:aggregate
          metrics: n_machines_flagged=3
          artifacts: summary_json, cross_sensor_png
```

Queries DS will actually run in the UI:

- **"Which model/hp combo maxes recall on MetroPT-3?"** filter by `tags.kyp.task_type = "detect"` and `params.dataset = "metropt3"`, sort by `metrics.recall`.
- **"Compare last two pipeline runs on the same dataset."** filter by `tags.kyp.pipeline = "default"`, check the two latest parent runs.
- **"Why did yesterday's run fail?"** open parent run with `tags.status = "failed"`, find the child stage run with `tags.status = "failed"`, open its traceback artifact.

## Failure semantics

| Failure level | Behavior |
|---|---|
| Single task fails | Logged as grandchild run with `status=failed` + traceback. Other tasks in the stage continue (Ray Tune-style fault tolerance). Stage marked failed if any task failed. |
| Entire stage fails | Pipeline aborts. Subsequent stages not submitted. MLflow parent tagged `status=failed`. Exit 1. |
| RayJob submission fails (K8s error) | `kyp` reports and exits 1 immediately. MLflow parent tagged `status=failed_submit`. |
| MLflow server unreachable | `kyp` refuses to start. (Fail-fast; tracking is a hard dependency, not optional.) |

DS can force `--continue-on-task-failure=false` to make the stage abort on the first failed task. Default is `true` — fan-outs are usually more valuable with partial results than no results.

## Local backend path

Same pipeline, same stages, same tasks. Differences:

- No RayJob, no K8s. `kyp pipeline run --backend=local` runs everything in one Python process.
- `LocalExecutor` replaces `RayExecutor`. Sequential `for` loop, full stack traces in the terminal.
- MLflow can point at a local server (`mlflow ui`) or the cluster server — profile decides.
- Data root is `file://./data/` by default. Uses the same `fsspec`-based I/O as cluster runs.

This is **the** debugging path. If something fails on minikube, reproduce with `--backend=local` on the same config and iterate. If `--backend=local` works but `--backend=ray` fails, the bug is in serialization/deps — not algorithm.

## Sweep path

`kyp sweep <config>` uses Ray Tune with `OptunaSearch`:

```python
# kyp/sweep.py (sketch)
def run_sweep(cfg_path, profile):
    sweep_cfg = load_sweep_config(cfg_path)
    search_space = build_search_space(sweep_cfg.params)

    def trainable(hp):
        task = sweep_cfg.task_factory(hp)
        result = sweep_cfg.task_fn(task)
        return result.metrics

    tune.run(
        trainable,
        config=search_space,
        search_alg=OptunaSearch(metric=sweep_cfg.metric, mode=sweep_cfg.mode),
        num_samples=sweep_cfg.num_samples,
        max_concurrent_trials=sweep_cfg.concurrency,
        resources_per_trial=sweep_cfg.resources,
        callbacks=[MLflowLoggerCallback(
            tracking_uri=os.environ["MLFLOW_TRACKING_URI"],
            experiment_name=os.environ["MLFLOW_EXPERIMENT_NAME"],
            save_artifact=True,
        )],
    )
```

Sweep runs land in MLflow as a parent (the sweep) with one child per trial. Best trial tagged on parent.

## What DS never touches

- `kyp stage-exec` (internal)
- RayJob manifests (rendered)
- MLflow API (wrapped)
- `kubectl` (wrapped)
- Ray initialization (wrapped)
- Working-dir zipping and upload (wrapped)

What DS writes:

- Task functions (`@task`).
- Stage factories (`@stage`).
- Pipeline list (`@pipeline`).
- Pipeline/sweep/platform configs (YAML).
- Optional reduce function per stage.

That's the entire interface.
