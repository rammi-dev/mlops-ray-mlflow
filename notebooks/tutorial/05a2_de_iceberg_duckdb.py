# %% [markdown]
# # 05a2 — DE: Iceberg Tables, DuckDB, and MLflow Datasets
#
# **What you'll learn:**
# - Create an Apache Iceberg table (local SQLite catalog, Parquet files)
# - Write training / evaluation data to it
# - Read the table back with DuckDB for fast analytics
# - Log the Iceberg-backed data as an MLflow dataset with custom metadata
#   (table name, snapshot, schema) so it appears in the experiment's dataset
#   lineage
#
# **Prerequisites:** `pyiceberg[sql-sqlite,pyarrow]`, `duckdb`, `mlflow>=3.11`
# (all in `pyproject.toml`).

# %% [markdown]
# ## Setup

# %%
import sys
from pathlib import Path

# Make sibling config.py importable (VS Code or Jupyter, any cwd)
try:
    _HERE = Path(__vsc_ipynb_file__).parent
except NameError:
    _HERE = Path.cwd()
sys.path.insert(0, str(_HERE))

import mlflow
import pandas as pd
import pyarrow as pa
from sklearn.datasets import load_wine
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

from config import init_mlflow

init_mlflow(experiment_name="05b-iceberg-duckdb")

# %% [markdown]
# ## 1. Create an Iceberg catalog and table
#
# We use PyIceberg's **SQLite catalog** — zero infrastructure, persisted to a
# local file. In production you'd swap this for a REST, Hive, or Glue catalog.

# %%
from pyiceberg.catalog.sql import SqlCatalog

WAREHOUSE_DIR = (_HERE / "iceberg-warehouse").resolve()
WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)

catalog = SqlCatalog(
    "local",
    **{
        "uri": f"sqlite:///{WAREHOUSE_DIR}/catalog.db",
        "warehouse": str(WAREHOUSE_DIR),
    },
)

# Create a namespace (like a database/schema)
try:
    catalog.create_namespace("tutorial")
except Exception:
    pass  # already exists

print(f"✅ Catalog: SQLite @ {WAREHOUSE_DIR}/catalog.db")
print(f"   Namespaces: {catalog.list_namespaces()}")

# %% [markdown]
# ## 2. Write data into an Iceberg table

# %%
data = load_wine(as_frame=True)
df = data.frame.copy()
df["target_name"] = df["target"].map(dict(enumerate(data.target_names)))

train_df, test_df = train_test_split(
    df, test_size=0.2, random_state=42, stratify=df["target"]
)
train_df = train_df.reset_index(drop=True)
test_df = test_df.reset_index(drop=True)

TABLE_NAME = "tutorial.wine_features"

# Convert to PyArrow and write
train_arrow = pa.Table.from_pandas(train_df)

try:
    catalog.drop_table(TABLE_NAME)
except Exception:
    pass

table = catalog.create_table(TABLE_NAME, schema=train_arrow.schema)
table.append(train_arrow)

# Also append test data as a second commit (Iceberg tracks snapshots)
test_arrow = pa.Table.from_pandas(test_df)
table.append(test_arrow)

table.refresh()
print(f"✅ Wrote {TABLE_NAME}")
print(f"   Total rows: {len(table.scan().to_pandas())}")
print(f"   Snapshots:  {len(table.metadata.snapshots)}")
print(f"   Current snapshot: {table.metadata.current_snapshot_id}")

# %% [markdown]
# ## 3. Read the table with DuckDB
#
# DuckDB can query Parquet files directly. We point it at the data files
# listed in the Iceberg snapshot — no special DuckDB Iceberg extension needed,
# just plain Parquet reads.

# %%
import duckdb

# Read via PyIceberg → Arrow → DuckDB (works with any catalog)
arrow_table = table.scan().to_arrow()
con = duckdb.connect()
result = con.execute("SELECT target_name, COUNT(*) as cnt FROM arrow_table GROUP BY target_name ORDER BY cnt DESC").fetchdf()
print("DuckDB query — class distribution:")
print(result)
print(f"\n✅ DuckDB read {len(arrow_table)} rows from Iceberg table")

# %% [markdown]
# ## 4. Log as MLflow datasets with Iceberg metadata
#
# We create MLflow datasets from the train/test DataFrames but enrich the
# `source` with the Iceberg table name and snapshot — making the lineage
# trace back to the exact table version, not just a file path.

# %%
iceberg_source = (
    f"iceberg://{TABLE_NAME}"
    f"?snapshot_id={table.metadata.current_snapshot_id}"
    f"&warehouse={WAREHOUSE_DIR}"
)

train_dataset = mlflow.data.from_pandas(
    train_df,
    source=iceberg_source,
    name="wine-train",
    targets="target",
)

test_dataset = mlflow.data.from_pandas(
    test_df,
    source=iceberg_source,
    name="wine-test",
    targets="target",
)

print(f"Train  digest={train_dataset.digest}")
print(f"Test   digest={test_dataset.digest}")
print(f"Source: {iceberg_source}")

# %% [markdown]
# ## 5. Train + log a run with dataset lineage

# %%
feature_cols = [c for c in train_df.columns if c not in ("target", "target_name")]

with mlflow.start_run(run_name="gb-wine-iceberg") as run:
    mlflow.log_input(train_dataset, context="training")
    mlflow.log_input(test_dataset, context="evaluation")

    mlflow.set_tags({
        "iceberg_table": TABLE_NAME,
        "iceberg_snapshot": str(table.metadata.current_snapshot_id),
        "data_format": "iceberg",
    })

    X_train = train_df[feature_cols]
    y_train = train_df["target"]
    X_test = test_df[feature_cols]
    y_test = test_df["target"]

    model = GradientBoostingClassifier(n_estimators=100, random_state=42)
    model.fit(X_train, y_train)

    acc = accuracy_score(y_test, model.predict(X_test))
    mlflow.log_metric("test_accuracy", acc)
    model_info = mlflow.sklearn.log_model(model, name="model")
    mlflow.set_tag("model_uri", model_info.model_uri)

    print(f"✅ Logged run {run.info.run_id}")
    print(f"   Accuracy: {acc:.4f}")
    print(f"   Datasets: wine-train, wine-test (Iceberg-backed)")

# %% [markdown]
# ## 6. Verify — inspect the run's dataset inputs
#
# The MLflow UI (Experiments → run → Overview) shows the datasets with their
# Iceberg source URI and digest. Programmatically:

# %%
run_info = mlflow.get_run(run.info.run_id)
print("Datasets on this run:")
for di in run_info.inputs.dataset_inputs:
    ds = di.dataset
    ctx = next((t.value for t in di.tags if t.key == "mlflow.data.context"), "?")
    print(f"  {ds.name}  context={ctx}  digest={ds.digest}")
    print(f"    source={ds.source[:120]}…")
