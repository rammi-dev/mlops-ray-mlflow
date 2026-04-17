# %% [markdown]
# # 05b2 — DS: Consume a Dask-prepared Dataset + Train with Dask on Ray
#
# **Role:** Data Scientist — consume the curated dataset produced by DE
# (template 05a3), do additional feature selection / sampling using Dask
# on Ray, train a model, and log everything to MLflow.
#
# **Key idea:** Dask gives you pandas compatibility; Ray gives you the
# executor. You write `ddf.groupby(...)`, `ddf[col].mean()`, etc. — same
# API as pandas — but it runs in parallel on Ray workers. No new API to
# learn, no Spark.
#
# **Prerequisite:** Run 05a3 first to produce the curated Parquet dataset
# at `datasets/sensor_features_dask/`.

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
import dask.dataframe as dd
import mlflow
import pandas as pd
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, accuracy_score, classification_report

from config import init_mlflow

init_mlflow(experiment_name="05b2-ds-dask-on-ray")

# %% [markdown]
# ## 1. Start Ray + Dask-on-Ray

# %%
if ray.is_initialized():
    ray.shutdown()
_rc = ray.init()
print(f"✅ Ray: {_rc.dashboard_url}")

from ray.util.dask import enable_dask_on_ray, disable_dask_on_ray
enable_dask_on_ray()
print("✅ Dask-on-Ray scheduler active")

# %% [markdown]
# ## 2. Load the DE-curated dataset
#
# The DE (template 05a3) wrote a partitioned Parquet directory. Dask reads
# it lazily — each part file is a partition executed on a Ray worker.

# %%
CURATED_PATH = (_HERE / "datasets" / "sensor_features_dask").resolve()
assert CURATED_PATH.exists(), (
    f"Dataset not found at {CURATED_PATH}. Run 05a3 first to produce it."
)

ddf = dd.read_parquet(str(CURATED_PATH))
print(f"Loaded: {ddf.npartitions} partitions, {len(ddf.columns)} columns")
print(ddf.dtypes)

# %% [markdown]
# ## 3. Explore with Dask (pandas-compatible, runs on Ray)
#
# Everything below is lazy — `.compute()` triggers execution on Ray workers.

# %%
# Class balance
print("Label distribution:")
print(ddf["label"].value_counts().compute())

# Per-sensor stats (groupby — executed across Ray workers)
sensor_stats = ddf.groupby("sensor_id").agg({
    "temperature": "mean",
    "pressure": "std",
    "vibration": "mean",
    "label": "mean",
}).compute()
print("\nPer-sensor summary:")
print(sensor_stats.round(3))

# %% [markdown]
# ## 4. Feature selection + sampling with Dask
#
# DS decides which columns to keep and optionally downsamples. Still using
# Dask's pandas API — no context switch.

# %%
FEATURE_COLS = [
    "temperature", "pressure", "vibration",
    "temp_rolling_mean", "vib_rolling_std", "temp_zscore",
    "temp_x_pressure", "pressure_bin",
]
TARGET = "label"

# Keep only feature + target columns
ddf_selected = ddf[FEATURE_COLS + [TARGET]]

# Drop any NaN rows from rolling features (first window)
ddf_clean = ddf_selected.dropna()
print(f"After dropna: ~{len(ddf_clean)} rows")

# %% [markdown]
# ## 5. Materialise + train/test split
#
# `.compute()` collects to pandas on the driver. For very large data, you'd
# keep in Dask and use `dask-ml` or `ray.data` for distributed training.

# %%
pdf = ddf_clean.compute()
print(f"Materialised {len(pdf)} rows, {len(pdf.columns)} columns")

train_df, test_df = train_test_split(
    pdf, test_size=0.2, random_state=42, stratify=pdf[TARGET]
)
train_df = train_df.reset_index(drop=True)
test_df = test_df.reset_index(drop=True)

# Save splits for reproducibility
DATA_DIR = (_HERE / "datasets").resolve()
TRAIN_PATH = DATA_DIR / "sensor_dask_train.parquet"
TEST_PATH = DATA_DIR / "sensor_dask_test.parquet"
train_df.to_parquet(TRAIN_PATH, index=False)
test_df.to_parquet(TEST_PATH, index=False)

# %% [markdown]
# ## 6. Log datasets + train + log to MLflow

# %%
train_dataset = mlflow.data.from_pandas(
    train_df, source=TRAIN_PATH.as_uri(),
    name="sensor-dask-train", targets=TARGET,
)
test_dataset = mlflow.data.from_pandas(
    test_df, source=TEST_PATH.as_uri(),
    name="sensor-dask-test", targets=TARGET,
)

with mlflow.start_run(run_name="gb-sensor-dask") as run:
    mlflow.log_input(train_dataset, context="training")
    mlflow.log_input(test_dataset, context="evaluation")

    mlflow.set_tags({
        "pipeline": "dask-on-ray",
        "role": "DS",
        "source_dataset": "sensor-features-curated (05a3)",
        "n_features": len(FEATURE_COLS),
    })

    params = {"n_estimators": 200, "max_depth": 5, "random_state": 42}
    mlflow.log_params(params)

    model = GradientBoostingClassifier(**params)
    model.fit(train_df[FEATURE_COLS], train_df[TARGET])

    preds = model.predict(test_df[FEATURE_COLS])
    metrics = {
        "test_f1": f1_score(test_df[TARGET], preds),
        "test_accuracy": accuracy_score(test_df[TARGET], preds),
    }
    mlflow.log_metrics(metrics)

    model_info = mlflow.sklearn.log_model(model, name="model")
    mlflow.set_tag("model_uri", model_info.model_uri)

    print(f"✅ Logged run {run.info.run_id}")
    print(f"   {metrics}")

print("\nClassification report:")
print(classification_report(test_df[TARGET], preds))

# %% [markdown]
# ## 7. Verify dataset lineage

# %%
run_info = mlflow.get_run(run.info.run_id)
print("Datasets:")
for di in run_info.inputs.dataset_inputs:
    ds = di.dataset
    ctx = next((t.value for t in di.tags if t.key == "mlflow.data.context"), "?")
    print(f"  {ds.name}  context={ctx}  digest={ds.digest}")

# %%
disable_dask_on_ray()
ray.shutdown()
print("✅ Done")
