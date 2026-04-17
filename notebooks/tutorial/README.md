# MLflow + Ray — `kyper-framework` Tutorial Templates

Reference templates for reproducible, tracked, distributed ML work on the
`kyper-framework` platform. The same `.py` / `.ipynb` files run locally
under a `uv` venv **and** inside the in-cluster JupyterHub singleuser pod.

---

## Stack

| Layer | Local (dev) | In-cluster (`kyper-framework`) |
|---|---|---|
| Compute | Local Ray (`ray.init()`) | Per-user `RayCluster` in `ds-workloads` |
| Experiment tracking | MLflow local file store (`./mlruns`) | In-cluster MLflow (`svc/mlflow` in `ds-platform`, port 5000) |
| Distributed training | Ray Train | Ray Train on the per-user cluster |
| Hyperparameter search | Ray Tune | Ray Tune on the per-user cluster |
| Model registry | MLflow Model Registry (same backend as above) | MLflow Model Registry |
| Serving | `mlflow models serve` (local REST) | `mlflow models serve` / `build-docker` |

Switch between the two by setting (or leaving unset) `MLFLOW_TRACKING_URI` —
see the repo-root README's **"Local development"** section for the port-forward
recipe.

---

## Templates

| File | What it teaches |
|---|---|
| `config.py` | `init_mlflow()` — env-driven tracking URI, thread-safe guard |
| `01_mlflow_tracking_basics.py` | Log params, metrics, tags, artifacts |
| `02_ray_tune_hpo.py` | Parallel hyperparameter search with Ray Tune |
| `03_ray_train_distributed.py` | Multi-worker training with Ray Train |
| `04_model_registry_deploy.py` | MLflow Model Registry + alias lifecycle (`@champion` / `@challenger`) |
| **05 — Datasets** | |
| `05a1_de_prepare_dataset.py` | DE role: curate + store + log a dataset for downstream ML |
| `05a2_de_iceberg_duckdb.py` | DE role: Iceberg table (SQLite catalog) + DuckDB query + MLflow dataset |
| `05a3_de_dask_on_ray.py` | DE role: ETL with Dask (pandas API) on Ray executor → Parquet + MLflow dataset |
| `05b1_ds_consume_dataset.py` | DS role: consume a DE-curated dataset in an ML experiment |
| `05b2_ds_dask_on_ray.py` | DS role: consume 05a3's Dask output, feature-select, train + log to MLflow |
| `06_mlflow_model_serve.py` | Signature, `pyfunc` load, `mlflow models serve` REST |

Every `.py` has a `.ipynb` sibling kept in sync via jupytext.

---

## Setup

### Local (uv venv)

These templates run in the same `.venv` as the reference notebook
([`anomaly_detection_ray_parallel.ipynb`](../anomaly_detection_ray_parallel.ipynb)).
If you haven't set it up yet:

```bash
cd /home/rami/Work/kyper
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e .    # installs all deps from pyproject.toml
```

Launch JupyterLab or open any `.ipynb` in VS Code and select
`/home/rami/Work/kyper/.venv/bin/python` as the kernel. Full setup and
MLflow-backend options are in the repo-root [`README.md`](../../README.md)
("Local development").

To point at the cluster MLflow instead of the local file store:

```bash
kubectl port-forward -n ds-platform svc/mlflow 5000:5000
export MLFLOW_TRACKING_URI=http://localhost:5000
```

Leave `MLFLOW_TRACKING_URI` unset to use the local SQLite store at
`kyper-framework/mlflow.db` (shared with the anomaly-detection notebook).
View with:
```bash
mlflow ui --backend-store-uri sqlite:///$(pwd)/kyper-framework/mlflow.db --port 5000
```

### In-cluster (JupyterHub)

Log in to the JupyterHub at your cluster's hub URL. The singleuser pod has
`MLFLOW_TRACKING_URI` and `RAY_ADDRESS` pre-injected — `init_mlflow()` and
`ray.init()` just work.

### Regenerating notebooks

```bash
uv pip install jupytext
jupytext --to notebook 01_mlflow_tracking_basics.py
```

---

## Naming conventions

- **Experiment**: one per project/objective (e.g. `churn-prediction`)
- **Run**: one per training attempt (named `{model}-{date}-{short-hash}`)
- **Tags**: always include `owner`, `dataset_version`, `env`

---

## Rules for the team

1. **Never use the default MLflow experiment** — always call `mlflow.set_experiment()` (handled by `init_mlflow`).
2. **Always log the dataset** — either as a tag/hash or via `mlflow.log_input(...)` (see template 05).
3. **Log artifacts** (plots, confusion matrices) not just scalars.
4. **End your runs** — use `with mlflow.start_run()` context managers, not manual `.end_run()`.
