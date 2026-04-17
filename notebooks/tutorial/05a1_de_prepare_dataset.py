# %% [markdown]
# # 05a — DE role: prepare a curated dataset for DS
#
# **Role:** data engineer (or senior DS) curating a dataset once, to be
# reused by many downstream ML experiments.
#
# **What this produces:**
# - A versioned Parquet file under a shared location (NFS in-cluster,
#   local `./shared_datasets/` here).
# - A companion `*.manifest.json` with schema, digest, row count, creator,
#   created-at — the "index card" a DS reads to pick a dataset.
#
# **Design choices:**
# - **Filename is the version.** `customers-v1.parquet`, `customers-v2.parquet` —
#   never overwrite. Immutable datasets keep training runs reproducible.
# - **No MLflow logging here.** Dataset preparation is ETL, not an experiment.
#   MLflow comes in on the DS side (template 05b), where training runs
#   *reference* the dataset URI via `mlflow.log_input`.
# - **Manifest next to the data.** A plain JSON file is easier than a
#   catalog service for a PoC, and a DS can cat it in a notebook.
#
# In the cluster, swap `DATASETS_ROOT` to `/mnt/oxy/datasets/` — the NFS
# RWX PVC mounted in every JupyterHub singleuser pod and every Ray pod.

# %% [markdown]
# ## Setup

# %%
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sklearn.datasets import load_breast_cancer
from sklearn.model_selection import train_test_split

# Local dev default; in-cluster set: DATASETS_ROOT=/mnt/oxy/datasets
DATASETS_ROOT = Path(
    os.environ.get("DATASETS_ROOT", "./shared_datasets")
).resolve()
DATASETS_ROOT.mkdir(parents=True, exist_ok=True)

DATASET_NAME = "breast-cancer"
OWNER        = os.environ.get("USER", "de-team")


def next_version(name: str, root: Path = DATASETS_ROOT) -> str:
    """
    Scan `root` for existing `{name}-v{N}-*.parquet` files and return the
    next unused version, e.g. "v1" if none exist, "v4" if v3 is the max.
    Datasets are immutable — every run produces a new version.
    """
    import re
    pattern = re.compile(rf"^{re.escape(name)}-v(\d+)-.*\.parquet$")
    used = [
        int(m.group(1))
        for p in root.glob(f"{name}-v*-*.parquet")
        if (m := pattern.match(p.name))
    ]
    return f"v{max(used, default=0) + 1}"


VERSION = next_version(DATASET_NAME)

print(f"Datasets root: {DATASETS_ROOT}")
print(f"Preparing:     {DATASET_NAME}-{VERSION}  by {OWNER}  "
      f"(auto-incremented)")

# %% [markdown]
# ## 1. Load raw → clean → split
#
# In a real pipeline this is the full ETL: read from source systems,
# join, dedupe, feature-engineer. Here we stub it with sklearn.

# %%
raw = load_breast_cancer(as_frame=True).frame
# Example light cleaning — drop exact duplicates, sanity-check target
raw = raw.drop_duplicates().reset_index(drop=True)
assert raw["target"].isin([0, 1]).all(), "unexpected target values"

train_df, test_df = train_test_split(
    raw, test_size=0.2, random_state=42, stratify=raw["target"]
)
train_df = train_df.reset_index(drop=True)   # stable bytes on write
test_df  = test_df.reset_index(drop=True)

print(f"Rows — train: {len(train_df)}   test: {len(test_df)}")

# %% [markdown]
# ## 2. Write versioned Parquet + manifest
#
# One file per split, each with its own manifest. Anything downstream
# reads the manifest first to discover schema + digest.

# %%
def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def publish_split(df: pd.DataFrame, split: str) -> dict:
    """
    Write `df` as {DATASET_NAME}-{VERSION}-{split}.parquet plus a
    sibling manifest. Returns the manifest dict.
    """
    fname = f"{DATASET_NAME}-{VERSION}-{split}.parquet"
    path  = DATASETS_ROOT / fname

    if path.exists():
        raise FileExistsError(
            f"{path} already exists. Datasets are immutable — "
            f"bump VERSION (currently '{VERSION}') instead of overwriting."
        )

    df.to_parquet(path, index=False)

    manifest = {
        "name":         DATASET_NAME,
        "version":      VERSION,
        "split":        split,
        "uri":          path.as_uri(),
        "n_rows":       len(df),
        "n_cols":       df.shape[1],
        "columns":      list(df.columns),
        "dtypes":       {c: str(t) for c, t in df.dtypes.items()},
        "targets":      ["target"],
        "sha256":       _sha256_file(path),
        "size_bytes":   path.stat().st_size,
        "created_by":   OWNER,
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "description":  "Breast-cancer classification, cleaned + split.",
    }

    manifest_path = path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


train_manifest = publish_split(train_df, "train")
test_manifest  = publish_split(test_df,  "test")

print(f"✅ Wrote {train_manifest['uri']}")
print(f"✅ Wrote {test_manifest['uri']}")

# %% [markdown]
# ## 3. What the DS will see
#
# A DS browsing `DATASETS_ROOT` finds a parquet and a manifest side by side.
# They read the manifest, pick a dataset, and consume it in their
# MLflow experiment (template 05b).

# %%
print(json.dumps(train_manifest, indent=2))

# %% [markdown]
# ## 4. Bonus — a tiny index for the datasets root
#
# Not a catalog service, just a glob that prints what's available.
# You can paste this into a notebook to let DS users "shop" for datasets.

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
            "size_MB": round(meta["size_bytes"] / 1024 / 1024, 2),
            "owner":   meta["created_by"],
            "uri":     meta["uri"],
        })
    return pd.DataFrame(rows)


print(list_datasets())

# %% [markdown]
# ## 5. Log dataset lineage to MLflow
#
# Optional but recommended: track each dataset version as an MLflow run in
# a dedicated `etl-datasets` experiment. This gives the ETL side an audit
# trail searchable alongside ML runs — "who produced this version, when,
# from which source, how big".
#
# Design:
# - **One experiment for all dataset prep:** `etl-datasets`.
# - **One run per (dataset, version, split):** params carry the
#   identifiers, metrics carry size, tags carry ownership/git SHA,
#   the manifest is attached as an artifact.
# - **Separate from ML experiments:** DS-side training runs live in their
#   own experiments; the dataset URI + digest link the two worlds.

# %%
import subprocess
import mlflow
from config import init_mlflow

init_mlflow(experiment_name="etl-datasets")


def _git_sha(default: str = "unknown") -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return default


def log_dataset_prep_to_mlflow(manifest: dict, parquet_path: Path) -> str:
    """
    Create one MLflow run describing this dataset version.
    Returns the run ID so DS consumers can reference it.
    """
    run_name = f"{manifest['name']}-{manifest['version']}-{manifest['split']}"

    # Build a `mlflow.data.Dataset` too — lets search_runs find every ML
    # run that trained on this dataset by digest, not just by tag.
    df_for_digest = pd.read_parquet(manifest["uri"].replace("file://", ""))
    dataset = mlflow.data.from_pandas(
        df_for_digest,
        source=manifest["uri"],
        name=run_name,
        targets="target",
    )

    with mlflow.start_run(run_name=run_name) as run:
        mlflow.set_tags({
            "etl.stage":    "dataset-prep",
            "dataset.name":    manifest["name"],
            "dataset.version": manifest["version"],
            "dataset.split":   manifest["split"],
            "dataset.owner":   manifest["created_by"],
            "git_sha":         _git_sha(),
        })
        mlflow.log_params({
            "dataset_name":    manifest["name"],
            "dataset_version": manifest["version"],
            "split":           manifest["split"],
            "source_uri":      manifest["uri"],
            "n_cols":          manifest["n_cols"],
            "targets":         ",".join(manifest["targets"]),
        })
        mlflow.log_metrics({
            "n_rows":     manifest["n_rows"],
            "size_bytes": manifest["size_bytes"],
        })

        # Log the dataset itself (digest + source) and attach the manifest
        # file as an artifact so it travels with the run.
        mlflow.log_input(dataset, context="output")
        mlflow.log_artifact(
            str(parquet_path.with_suffix(".manifest.json")),
            artifact_path="manifest",
        )

        print(f"  ✅ MLflow run {run.info.run_id}  ({run_name})")
        return run.info.run_id


train_run_id = log_dataset_prep_to_mlflow(
    train_manifest, DATASETS_ROOT / f"{DATASET_NAME}-{VERSION}-train.parquet"
)
test_run_id = log_dataset_prep_to_mlflow(
    test_manifest, DATASETS_ROOT / f"{DATASET_NAME}-{VERSION}-test.parquet"
)

print("\n✅ Dataset prep logged to MLflow experiment 'etl-datasets'.")
print(f"   Train prep run: {train_run_id}")
print(f"   Test  prep run: {test_run_id}")

# %% [markdown]
# ## 6. Query dataset prep history
#
# DS can now ask MLflow "show me every version of `breast-cancer`":

# %%
history = mlflow.search_runs(
    experiment_names=["etl-datasets"],
    filter_string=f"tags.`dataset.name` = '{DATASET_NAME}'",
    order_by=["start_time DESC"],
)
if len(history):
    cols = ["tags.dataset.version", "tags.dataset.split",
            "tags.dataset.owner", "metrics.n_rows",
            "metrics.size_bytes", "tags.git_sha", "start_time"]
    present = [c for c in cols if c in history.columns]
    print(history[present].head(10))
else:
    print("(no runs yet — run this notebook to populate)")
