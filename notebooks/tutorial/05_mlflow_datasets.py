# %% [markdown]
# # 05 — Tracking Datasets with MLflow
#
# **What you'll learn:**
# - **Store** a dataset to disk (Parquet)
# - Build a `mlflow.data.Dataset` pointing at the stored file
# - Log it to a run with `mlflow.log_input(dataset, context="training")`
# - **Read** the dataset back from the MLflow source (round-trip)
# - Inspect digest + schema, and query runs by digest
#
# Dataset tracking closes the "what data was this trained on?" loop —
# the digest is a stable hash of the dataframe contents, and the `source`
# is the URI you can re-load from later.

# %% [markdown]
# ## Setup

# %%
from pathlib import Path

import mlflow
import pandas as pd
from mlflow.data.pandas_dataset import PandasDataset
from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

# Make sibling config.py importable (VS Code or Jupyter, any cwd)
import sys
from pathlib import Path
try:
    _HERE = Path(__vsc_ipynb_file__).parent
except NameError:
    _HERE = Path.cwd()
sys.path.insert(0, str(_HERE))

from config import init_mlflow

init_mlflow(experiment_name="05-mlflow-datasets")

# %% [markdown]
# ## 1. Store the dataset on disk
#
# We keep the dataset as a Parquet file under `./datasets/`. In the cluster
# you'd write to the shared NFS mount `/mnt/oxy/datasets/` instead — the
# rest of the notebook is identical, just change `DATA_ROOT`.
#
# Parquet is preferred over CSV for typed columns, smaller files, and a
# faster `read_parquet` round-trip. Install once: `uv pip install pyarrow`.

# %%
DATA_ROOT = Path("./datasets").resolve()
DATA_ROOT.mkdir(parents=True, exist_ok=True)

data = load_breast_cancer(as_frame=True)
df = data.frame
train_df, test_df = train_test_split(
    df, test_size=0.2, random_state=42, stratify=df["target"]
)
# Reset indices so the in-memory DataFrames match what Parquet stores
# (we write with `index=False`). Without this, the digest of the logged
# dataset differs from a reloaded-from-parquet copy — round-trip breaks.
train_df = train_df.reset_index(drop=True)
test_df  = test_df.reset_index(drop=True)

TRAIN_PATH = DATA_ROOT / "breast-cancer-train.parquet"
TEST_PATH  = DATA_ROOT / "breast-cancer-test.parquet"

train_df.to_parquet(TRAIN_PATH, index=False)
test_df.to_parquet(TEST_PATH,  index=False)

print(f"✅ Wrote {TRAIN_PATH}  ({len(train_df)} rows)")
print(f"✅ Wrote {TEST_PATH}   ({len(test_df)} rows)")

# %% [markdown]
# ## 2. Build MLflow datasets pointing at the stored files
#
# The `source` is the URI MLflow records with the run — use the absolute
# file path (or `s3://`, `gs://`, `dbfs:/` in cloud setups) so anyone with
# the run ID can find and reload the exact bytes.

# %%
train_dataset: PandasDataset = mlflow.data.from_pandas(
    train_df,
    source=TRAIN_PATH.as_uri(),         # e.g. file:///home/rami/.../train.parquet
    name="breast-cancer-train",
    targets="target",
)

test_dataset: PandasDataset = mlflow.data.from_pandas(
    test_df,
    source=TEST_PATH.as_uri(),
    name="breast-cancer-test",
    targets="target",
)

print(f"Train  digest={train_dataset.digest}  source={train_dataset.source.uri}")
print(f"Test   digest={test_dataset.digest}  source={test_dataset.source.uri}")
print(f"\nSchema (train):\n{train_dataset.schema}")

# %% [markdown]
# ## 3. Log a run that references both datasets

# %%
with mlflow.start_run(run_name="rf-with-datasets") as run:
    mlflow.log_input(train_dataset, context="training")
    mlflow.log_input(test_dataset,  context="evaluation")

    X_train = train_df.drop("target", axis=1)
    y_train = train_df["target"]
    X_test  = test_df.drop("target",  axis=1)
    y_test  = test_df["target"]

    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(X_train, y_train)

    acc = accuracy_score(y_test, model.predict(X_test))
    mlflow.log_metric("test_accuracy", acc)
    mlflow.sklearn.log_model(model, name="model")

    run_id = run.info.run_id
    print(f"✅ Logged run {run_id} with datasets + accuracy={acc:.4f}")

# %% [markdown]
# ## 4. Read the dataset back from the run
#
# Given only the run ID, recover the dataset source URI and reload the
# DataFrame. This is the "what data was this trained on?" round-trip.

# %%
run_info = mlflow.get_run(run_id)
print("Dataset inputs on this run:")
for dataset_input in run_info.inputs.dataset_inputs:
    ds = dataset_input.dataset
    tags = {t.key: t.value for t in dataset_input.tags}
    print(f"  name={ds.name}  context={tags.get('mlflow.data.context')}  "
          f"digest={ds.digest}  source={ds.source}")

# ── Pick the training dataset and reload it from its source URI ────────
import json
training_input = next(
    di for di in run_info.inputs.dataset_inputs
    if any(t.key == "mlflow.data.context" and t.value == "training" for t in di.tags)
)
# `source` is a JSON blob; for a PandasDataset backed by a file, it has "uri"
source_uri = json.loads(training_input.dataset.source)["uri"]
print(f"\nReloading from: {source_uri}")

reloaded_df = pd.read_parquet(source_uri.replace("file://", ""))
print(f"✅ Reloaded {len(reloaded_df)} rows, columns={list(reloaded_df.columns)[:5]}…")

# ── Sanity check: digest of reloaded data matches the logged digest ────
reloaded_ds = mlflow.data.from_pandas(
    reloaded_df, source=source_uri, name="breast-cancer-train", targets="target"
)
assert reloaded_ds.digest == training_input.dataset.digest, "digest mismatch!"
print(f"✅ Digest matches: {reloaded_ds.digest}")

# %% [markdown]
# ## 5. Query all runs that used a given dataset
#
# The digest is indexed — you can filter runs by it.
#
# > **NOTE:** `datasets.digest = '...'` only works against an SQL-backed
# > MLflow (i.e. the in-cluster MLflow). With the local `./mlruns` file
# > store this search returns 0 rows. To exercise this cell, port-forward
# > the cluster MLflow:
# > ```bash
# > kubectl port-forward -n ds-platform svc/mlflow 5000:5000
# > export MLFLOW_TRACKING_URI=http://localhost:5000
# > ```

# %%
runs_df = mlflow.search_runs(
    experiment_names=["05-mlflow-datasets"],
    filter_string=f"datasets.digest = '{train_dataset.digest}'",
)
print(f"Runs trained on digest {train_dataset.digest}: {len(runs_df)}")
if len(runs_df):
    print(runs_df[["run_id", "metrics.test_accuracy"]].head())
