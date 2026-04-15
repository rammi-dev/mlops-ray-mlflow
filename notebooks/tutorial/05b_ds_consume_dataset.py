# %% [markdown]
# # 05b — DS role: consume a DE-curated dataset in an ML experiment
#
# **Role:** data scientist running training on a dataset someone else
# prepared (template 05a). You don't re-split, re-clean, or re-download —
# you read the manifest, load the parquet, and log the URI as an MLflow
# input so anyone can trace "which bytes trained this model?".
#
# **What this demonstrates:**
# 1. Discovering available datasets (listing manifests)
# 2. Loading a chosen dataset by version
# 3. Registering it as `mlflow.data.Dataset` with the DE-supplied URI
# 4. `mlflow.log_input(..., context="training")` on the training run
# 5. Round-trip: recover the dataset URI from just the run ID
#
# **Pairs with:** `05a_de_prepare_dataset.py` — run that first, or point
# `DATASETS_ROOT` at an existing shared location (`/mnt/oxy/datasets` in
# the cluster).

# %% [markdown]
# ## Setup

# %%
import json
import os
from pathlib import Path

import mlflow
import pandas as pd
from mlflow.data.pandas_dataset import PandasDataset
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

# Make sibling config.py importable (VS Code or Jupyter, any cwd)
import sys
try:
    _HERE = Path(__vsc_ipynb_file__).parent
except NameError:
    _HERE = Path.cwd()
sys.path.insert(0, str(_HERE))

from config import init_mlflow

DATASETS_ROOT = Path(
    os.environ.get("DATASETS_ROOT", "./shared_datasets")
).resolve()

init_mlflow(experiment_name="05b-ds-consumes-curated-dataset")

# %% [markdown]
# ## 1. Browse available datasets
#
# DE publishes a manifest next to each parquet. Reading the manifests
# is enough to pick one — no need to open the parquet.

# %%
def list_datasets(root: Path = DATASETS_ROOT) -> pd.DataFrame:
    rows = []
    for m in sorted(root.glob("*.manifest.json")):
        meta = json.loads(m.read_text())
        rows.append({
            "name":    meta["name"],
            "version": meta["version"],
            "split":   meta["split"],
            "rows":    meta["n_rows"],
            "owner":   meta["created_by"],
            "uri":     meta["uri"],
        })
    return pd.DataFrame(rows)


available = list_datasets()
if available.empty:
    raise RuntimeError(
        f"No datasets found under {DATASETS_ROOT}. "
        "Run `05a_de_prepare_dataset.py` first."
    )
print(available)

# %% [markdown]
# ## 2. Pick a dataset version and load the train/test splits

# %%
DATASET_NAME = "breast-cancer"
VERSION      = "v1"

def load_split(name: str, version: str, split: str) -> tuple[pd.DataFrame, dict]:
    manifest_path = DATASETS_ROOT / f"{name}-{version}-{split}.manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No manifest at {manifest_path}. Available:\n{list_datasets()}"
        )
    manifest = json.loads(manifest_path.read_text())
    df = pd.read_parquet(manifest["uri"].replace("file://", ""))
    return df, manifest


train_df, train_manifest = load_split(DATASET_NAME, VERSION, "train")
test_df,  test_manifest  = load_split(DATASET_NAME, VERSION, "test")

print(f"Loaded {DATASET_NAME}-{VERSION}: "
      f"train={len(train_df)}  test={len(test_df)}")
print(f"Owner: {train_manifest['created_by']}  "
      f"created: {train_manifest['created_at']}")

# %% [markdown]
# ## 3. Register the datasets with MLflow
#
# `source=` is the DE-published URI — this is what makes "which bytes
# trained this model?" answerable from just a run ID.

# %%
train_ds: PandasDataset = mlflow.data.from_pandas(
    train_df,
    source=train_manifest["uri"],
    name=f"{DATASET_NAME}-{VERSION}-train",
    targets="target",
)
test_ds: PandasDataset = mlflow.data.from_pandas(
    test_df,
    source=test_manifest["uri"],
    name=f"{DATASET_NAME}-{VERSION}-test",
    targets="target",
)

print(f"Train digest: {train_ds.digest}")
print(f"Test  digest: {test_ds.digest}")

# %% [markdown]
# ## 4. Train — log the datasets as run inputs
#
# `mlflow.log_input` attaches the dataset (name, digest, source URI,
# schema) to the run. You'll see it on the run page under "Dataset".

# %%
with mlflow.start_run(run_name="rf-on-curated-dataset") as run:
    mlflow.log_input(train_ds, context="training")
    mlflow.log_input(test_ds,  context="evaluation")

    # Tag the dataset provenance so search_runs can find this later
    mlflow.set_tags({
        "dataset.name":    DATASET_NAME,
        "dataset.version": VERSION,
        "dataset.owner":   train_manifest["created_by"],
    })

    X_train = train_df.drop("target", axis=1)
    y_train = train_df["target"]
    X_test  = test_df.drop("target",  axis=1)
    y_test  = test_df["target"]

    model = RandomForestClassifier(n_estimators=200, random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)

    acc = accuracy_score(y_test, model.predict(X_test))
    mlflow.log_metric("test_accuracy", acc)

    model_info = mlflow.sklearn.log_model(model, name="model")
    mlflow.set_tag("model_uri", model_info.model_uri)

    run_id = run.info.run_id
    print(f"✅ Run {run_id}  accuracy={acc:.4f}")

# %% [markdown]
# ## 5. Round-trip — recover the dataset from just the run ID
#
# This is the point of the whole exercise: given a model you didn't
# train, answer "what data trained this?" without asking anyone.

# %%
run_info = mlflow.get_run(run_id)
for di in run_info.inputs.dataset_inputs:
    tags = {t.key: t.value for t in di.tags}
    print(f"  {di.dataset.name}  "
          f"context={tags.get('mlflow.data.context')}  "
          f"digest={di.dataset.digest}")

training_input = next(
    di for di in run_info.inputs.dataset_inputs
    if any(t.key == "mlflow.data.context" and t.value == "training"
           for t in di.tags)
)
source_uri = json.loads(training_input.dataset.source)["uri"]
print(f"\nTraining data lives at: {source_uri}")

# %% [markdown]
# ## 6. Find every run trained on this dataset version
#
# > **SQL-backed MLflow only** (cluster). With the local `./mlruns`
# > file store, filter on the tag we set instead: `tags.\"dataset.version\" = 'v1'`.

# %%
# Preferred — on cluster MLflow (CNPG Postgres backend):
# runs = mlflow.search_runs(
#     experiment_names=["05b-ds-consumes-curated-dataset"],
#     filter_string=f"datasets.digest = '{train_ds.digest}'",
# )

# Works everywhere (file store + SQL), using the tag we attached above:
runs = mlflow.search_runs(
    experiment_names=["05b-ds-consumes-curated-dataset"],
    filter_string=(f"tags.`dataset.name` = '{DATASET_NAME}' "
                   f"and tags.`dataset.version` = '{VERSION}'"),
)
print(f"Runs on {DATASET_NAME}-{VERSION}: {len(runs)}")
if len(runs):
    print(runs[["run_id", "metrics.test_accuracy", "start_time"]].head())
