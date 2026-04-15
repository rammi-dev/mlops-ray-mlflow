# %% [markdown]
# # 03 — Parallel Cross-Validation with Ray
#
# **What you'll learn:**
# - Parallelise scikit-learn CV across Ray workers via `@ray.remote`
# - Aggregate fold metrics and log them to MLflow
# - Train a final model on the full dataset and log it
# - Scale from 1 to N workers by changing `N_SPLITS`
#
# **Why not `SklearnTrainer`?** Ray Train's sklearn integration was
# deprecated and removed in Ray 2.10+. The `@ray.remote` pattern below
# is what it did under the hood — simpler, version-stable, same result.
#
# **Runtime:** Local Ray (dev) or in-cluster Ray (via kyper-framework JupyterHub)

# %% [markdown]
# ## Setup

# %%
import ray
import mlflow
import numpy as np
import pandas as pd
from sklearn.datasets import make_classification
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, accuracy_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, train_test_split

# Make sibling config.py importable (VS Code or Jupyter, any cwd)
import sys
from pathlib import Path
try:
    _HERE = Path(__vsc_ipynb_file__).parent
except NameError:
    _HERE = Path.cwd()
sys.path.insert(0, str(_HERE))

from config import init_mlflow

EXPERIMENT_NAME = "03-parallel-cv"
init_mlflow(experiment_name=EXPERIMENT_NAME)

# %% [markdown]
# ## Connect to Ray
#
# `ray.init()` with no address uses local Ray. In JupyterHub singleuser pods,
# `RAY_ADDRESS` is pre-set and `ray.init()` picks it up automatically.

# %%
if ray.is_initialized():
    ray.shutdown()
_rc = ray.init()
print(f"✅ Ray initialised — dashboard: {_rc.dashboard_url}")

# %% [markdown]
# ## Prepare data

# %%
X, y = make_classification(
    n_samples=50_000,
    n_features=30,
    n_informative=15,
    random_state=42,
)

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, stratify=y, random_state=42
)

DATASET_VERSION = "synthetic-50k-v1"
print(f"Train: {X_train.shape}  Test: {X_test.shape}")

model_params = {
    "n_estimators": 200,
    "max_depth": 8,
    "min_samples_leaf": 4,
    "n_jobs": 1,          # workers are Ray tasks — keep sklearn single-threaded
    "random_state": 42,
}

# %% [markdown]
# ## Parallel cross-validation with `@ray.remote`
#
# Each fold fits on a Ray worker. Put the full arrays in the object store
# once (`ray.put`) so workers share them instead of re-serialising per task.

# %%
N_SPLITS = 5

@ray.remote
def fit_fold(fold_idx, train_idx, val_idx, X_ref, y_ref, params):
    """Fit one CV fold on a Ray worker. Returns per-fold metrics."""
    X, y = X_ref, y_ref
    model = RandomForestClassifier(**params)
    model.fit(X[train_idx], y[train_idx])
    preds = model.predict(X[val_idx])
    proba = model.predict_proba(X[val_idx])[:, 1]
    return {
        "fold": fold_idx,
        "f1":       f1_score(y[val_idx], preds),
        "accuracy": accuracy_score(y[val_idx], preds),
        "roc_auc":  roc_auc_score(y[val_idx], proba),
    }

X_ref = ray.put(X_train)
y_ref = ray.put(y_train)

skf = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=42)
futures = [
    fit_fold.remote(i, train_idx, val_idx, X_ref, y_ref, model_params)
    for i, (train_idx, val_idx) in enumerate(skf.split(X_train, y_train))
]

fold_metrics = ray.get(futures)
for m in fold_metrics:
    print(f"  fold {m['fold']}: f1={m['f1']:.4f}  acc={m['accuracy']:.4f}  auc={m['roc_auc']:.4f}")

cv_summary = {
    "cv_f1_mean":       float(np.mean([m["f1"]       for m in fold_metrics])),
    "cv_f1_std":        float(np.std ([m["f1"]       for m in fold_metrics])),
    "cv_accuracy_mean": float(np.mean([m["accuracy"] for m in fold_metrics])),
    "cv_roc_auc_mean":  float(np.mean([m["roc_auc"]  for m in fold_metrics])),
}
print(f"\nCV summary: {cv_summary}")

# %% [markdown]
# ## Train final model on full train set + log to MLflow
#
# One MLflow parent run captures CV metrics, per-fold metrics, params, and
# the final fitted model.

# %%
with mlflow.start_run(run_name="rf-parallel-cv") as run:
    mlflow.set_tags({
        "owner": "your-name",
        "dataset_version": DATASET_VERSION,
        "n_splits": N_SPLITS,
        "env": "dev",
    })
    mlflow.log_params(model_params)
    mlflow.log_metrics(cv_summary)
    for m in fold_metrics:
        mlflow.log_metrics(
            {f"fold_{m['fold']}_f1": m["f1"],
             f"fold_{m['fold']}_accuracy": m["accuracy"]},
        )

    final_model = RandomForestClassifier(**model_params)
    final_model.fit(X_train, y_train)

    preds = final_model.predict(X_test)
    proba = final_model.predict_proba(X_test)[:, 1]
    test_metrics = {
        "test_f1":       f1_score(y_test, preds),
        "test_accuracy": accuracy_score(y_test, preds),
        "test_roc_auc":  roc_auc_score(y_test, proba),
    }
    mlflow.log_metrics(test_metrics)
    mlflow.sklearn.log_model(final_model, name="model")

    FINAL_RUN_ID = run.info.run_id
    print(f"\n✅ Logged run: {FINAL_RUN_ID}")
    print(f"   Test metrics: {test_metrics}")

# %%
ray.shutdown()
