# %% [markdown]
# # 05a3 — DE: Prepare a Dataset with Dask on Ray
#
# **Role:** Data Engineer — build an ETL pipeline using Dask's pandas-like
# API, executed on Ray workers. The result is a clean, feature-enriched
# Parquet dataset logged to MLflow for downstream DS consumption.
#
# **Key idea:** Dask gives you pandas compatibility at scale; Ray gives you
# the scheduler, resource management, and dashboard. Together you write
# familiar pandas-style code that runs distributed — no Spark, no new API.
#
# **Flow:**
# 1. `ray.init()` — start local (or cluster) Ray
# 2. `enable_dask_on_ray()` — Dask operations run on Ray workers
# 3. Load raw data into a Dask DataFrame
# 4. Transform / feature-engineer with Dask (lazy, parallel)
# 5. Write to Parquet (the "curated dataset")
# 6. Log to MLflow as a dataset with digest + source URI
#
# **Prerequisites:** `dask[dataframe]`, `ray[default]==2.41.0`, `mlflow`, `pyarrow`

# %% [markdown]
# ## Setup

# %%
import sys
from pathlib import Path

try:
    _HERE = Path(__vsc_ipynb_file__).parent
except NameError:
    _HERE = Path.cwd()
sys.path.insert(0, str(_HERE))

import ray
import dask
import dask.dataframe as dd
import mlflow
import pandas as pd
import numpy as np

from config import init_mlflow

init_mlflow(experiment_name="05a3-de-dask-on-ray")

# %% [markdown]
# ## 1. Start Ray + enable Dask-on-Ray
#
# After `enable_dask_on_ray()`, every `dask.dataframe` operation dispatches
# to Ray — no separate Dask scheduler or cluster needed.

# %%
if ray.is_initialized():
    ray.shutdown()
_rc = ray.init()
print(f"✅ Ray: {_rc.dashboard_url}")

from ray.util.dask import enable_dask_on_ray, disable_dask_on_ray
enable_dask_on_ray()
print("✅ Dask-on-Ray scheduler active")

# %% [markdown]
# ## 2. Load raw data into Dask
#
# Dask reads CSVs, Parquet, JSON lazily — each file/partition becomes a task.
# Here we generate synthetic raw data to simulate an ingestion pipeline.

# %%
N_ROWS = 300_000
N_PARTITIONS = 10

np.random.seed(42)
raw = pd.DataFrame({
    "sensor_id":   np.random.choice(["A", "B", "C", "D"], N_ROWS),
    "timestamp":   pd.date_range("2025-01-01", periods=N_ROWS, freq="s"),
    "temperature": np.random.normal(70, 5, N_ROWS),
    "pressure":    np.random.normal(1013, 10, N_ROWS),
    "vibration":   np.abs(np.random.normal(0, 2, N_ROWS)),
    "label":       np.random.choice([0, 1], N_ROWS, p=[0.95, 0.05]),
})

ddf = dd.from_pandas(raw, npartitions=N_PARTITIONS)
print(f"Raw Dask DataFrame: {ddf.npartitions} partitions, ~{len(ddf)} rows")
print(ddf.dtypes)

# %% [markdown]
# ## 3. Feature engineering with Dask (pandas-compatible)
#
# Everything below is **lazy** — actual computation happens at `.compute()`
# or `.to_parquet()`. Ray workers do the heavy lifting in parallel.

# %%
# Rolling stats per partition (no cross-partition shuffle)
ddf["temp_rolling_mean"] = ddf["temperature"].rolling(window=60, min_periods=1).mean()
ddf["vib_rolling_std"] = ddf["vibration"].rolling(window=60, min_periods=1).std()

# Z-score
temp_mean = ddf["temperature"].mean()   # lazy scalar
temp_std = ddf["temperature"].std()
ddf["temp_zscore"] = (ddf["temperature"] - temp_mean) / temp_std

# Interaction
ddf["temp_x_pressure"] = ddf["temperature"] * ddf["pressure"]

# Binning (per-partition map)
ddf["pressure_bin"] = ddf["pressure"].map_partitions(
    pd.cut, bins=5, labels=False
).astype("float64")

# Quick sanity stats (triggers compute on Ray)
stats = ddf[["temp_rolling_mean", "vib_rolling_std", "temp_zscore"]].describe().compute()
print("Feature stats (computed on Ray):")
print(stats.round(3))

# %% [markdown]
# ## 3b. See the parallelism — Dask on Ray vs single-threaded pandas
#
# This cell proves that Dask on Ray actually runs partitions in parallel.
# We time the same heavy operation two ways:
# 1. **Dask on Ray** — each partition processed by a separate Ray worker
# 2. **Plain pandas** — single-threaded on the driver
#
# Open the Ray dashboard (http://127.0.0.1:8265 → Tasks) while this runs
# to watch tasks fan out across workers.

# %%
import time

def heavy_transform(series):
    """Simulates a CPU-intensive per-row transform."""
    return series.rolling(200, min_periods=1).apply(lambda w: np.std(w) * np.mean(w), raw=True)

# ── Dask on Ray (parallel across partitions) ──────────────────────────────
t0 = time.perf_counter()
result_dask = ddf["temperature"].map_partitions(heavy_transform).compute()
t_dask = time.perf_counter() - t0

# ── Plain pandas (single-threaded) ───────────────────────────────────────
pdf_temp = ddf["temperature"].compute()  # materialise once
t0 = time.perf_counter()
result_pandas = heavy_transform(pdf_temp)
t_pandas = time.perf_counter() - t0

speedup = t_pandas / t_dask if t_dask > 0 else float("inf")
print(f"Dask on Ray ({ddf.npartitions} partitions): {t_dask:.2f}s")
print(f"Plain pandas (single-thread):               {t_pandas:.2f}s")
print(f"Speedup: {speedup:.1f}×")
print(f"\n💡 Open Ray dashboard → Tasks tab to see {ddf.npartitions} tasks run in parallel.")

# %% [markdown]
# ## 4. Write curated dataset to Parquet
#
# `ddf.to_parquet(...)` writes one Parquet file per partition — all via Ray
# workers. The result is a directory of Parquet part files, ready for any
# downstream tool (DuckDB, pandas, Spark, Dask again).

# %%
DATA_DIR = (_HERE / "datasets").resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
CURATED_PATH = DATA_DIR / "sensor_features_dask"

ddf.to_parquet(str(CURATED_PATH), engine="pyarrow", overwrite=True)
print(f"✅ Wrote curated dataset to {CURATED_PATH}/")
print(f"   Partitions: {len(list(CURATED_PATH.glob('*.parquet')))}")

# %% [markdown]
# ## 5. Log to MLflow as a dataset
#
# Read back a sample to compute digest + schema, then log with source
# pointing at the Parquet directory. DS can later reload from this URI.

# %%
# Read back for digest computation (full data, via Dask → pandas)
curated_df = dd.read_parquet(str(CURATED_PATH)).compute()

dataset = mlflow.data.from_pandas(
    curated_df,
    source=CURATED_PATH.as_uri(),
    name="sensor-features-curated",
    targets="label",
)

with mlflow.start_run(run_name="de-dask-etl") as run:
    mlflow.log_input(dataset, context="output")
    mlflow.set_tags({
        "pipeline": "dask-on-ray",
        "role": "DE",
        "n_rows": len(curated_df),
        "n_partitions": N_PARTITIONS,
        "n_features": len(curated_df.columns),
    })
    mlflow.log_metric("n_rows", len(curated_df))
    mlflow.log_metric("anomaly_rate", float(curated_df["label"].mean()))

    print(f"✅ Logged DE run {run.info.run_id}")
    print(f"   Dataset: {dataset.name}  digest={dataset.digest}")
    print(f"   Source:  {dataset.source.uri}")

# %% [markdown]
# ## 6. Verify — what the DS will see
#
# The DS (template 05b2) can now:
# ```python
# ddf = dd.read_parquet("<source_uri>")  # or pd.read_parquet(...)
# ```
# and know the exact digest/schema of the data they're training on.

# %%
run_info = mlflow.get_run(run.info.run_id)
for di in run_info.inputs.dataset_inputs:
    ds = di.dataset
    ctx = next((t.value for t in di.tags if t.key == "mlflow.data.context"), "?")
    print(f"  {ds.name}  context={ctx}  digest={ds.digest}")
    print(f"  source={ds.source[:100]}…")

# %%
disable_dask_on_ray()
ray.shutdown()
print("✅ Done — dataset ready for DS consumption")
