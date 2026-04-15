# %% [markdown]
# # 01 — MLflow Tracking Basics
#
# **What you'll learn:**
# - Log parameters, metrics, and tags
# - Log artifacts (plots, files)
# - Query past runs programmatically
# - Compare runs in the MLflow UI
#
# **Runtime:** Local (uv venv) or in-cluster JupyterHub

# %% [markdown]
# ## Setup

# %%
import mlflow
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.datasets import make_classification
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score,
    ConfusionMatrixDisplay
)

# Make sibling config.py importable (VS Code or Jupyter, any cwd)
import sys
from pathlib import Path
try:
    _HERE = Path(__vsc_ipynb_file__).parent
except NameError:
    _HERE = Path.cwd()
sys.path.insert(0, str(_HERE))

from config import init_mlflow  # shared config

init_mlflow(experiment_name="01-tracking-basics-demo")

# %% [markdown]
# ## Load data

# %%
X, y = make_classification(
    n_samples=5_000,
    n_features=20,
    n_informative=10,
    random_state=42,
)
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

DATASET_VERSION = "synthetic-v1"

# %% [markdown]
# ## Train & log a single run
#
# **Rule:** Always use `with mlflow.start_run()` — it guarantees the run
# is closed even if an exception occurs.

# %%
params = {
    "n_estimators": 100,
    "max_depth": 5,
    "min_samples_leaf": 4,
    "random_state": 42,
}

with mlflow.start_run(run_name="rf-baseline") as run:

    # ── 1. Log parameters ───────────────────────────────────────────────
    mlflow.log_params(params)

    # ── 2. Log metadata as tags ─────────────────────────────────────────
    mlflow.set_tags({
        "owner": "your-name",
        "dataset_version": DATASET_VERSION,
        "env": "dev",
        "model_type": "RandomForest",
    })

    # ── 3. Train ─────────────────────────────────────────────────────────
    model = RandomForestClassifier(**params)
    model.fit(X_train, y_train)

    # ── 4. Log metrics ───────────────────────────────────────────────────
    preds = model.predict(X_test)
    proba = model.predict_proba(X_test)[:, 1]

    metrics = {
        "accuracy":  accuracy_score(y_test, preds),
        "f1":        f1_score(y_test, preds),
        "roc_auc":   roc_auc_score(y_test, proba),
    }
    mlflow.log_metrics(metrics)
    print(f"Metrics: {metrics}")

    # ── 5. Log a metric over steps (e.g. OOB score per tree) ─────────────
    # Useful for tracking progress across epochs/iterations
    oob_scores = [0.72, 0.78, 0.81, 0.83, 0.84]  # example
    for step, score in enumerate(oob_scores):
        mlflow.log_metric("oob_score", score, step=step)

    # ── 6. Log artifact: confusion matrix plot ───────────────────────────
    fig, ax = plt.subplots(figsize=(5, 4))
    ConfusionMatrixDisplay.from_predictions(y_test, preds, ax=ax)
    ax.set_title("Confusion Matrix")
    fig.savefig("/tmp/confusion_matrix.png", bbox_inches="tight")
    mlflow.log_artifact("/tmp/confusion_matrix.png", artifact_path="plots")
    plt.close()

    # ── 7. Log the model ─────────────────────────────────────────────────
    model_info = mlflow.sklearn.log_model(model, name="model")
    mlflow.set_tag("model_uri", model_info.model_uri)

    print(f"\n✅ Run ID: {run.info.run_id}")
    print(f"   View in the MLflow UI (mlflow ui or cluster MLflow).")

# %% [markdown]
# ## Compare multiple runs
#
# Run this cell multiple times with different params to build up a comparison.

# %%
experiment_runs = [
    {"n_estimators": 50,  "max_depth": 3},
    {"n_estimators": 100, "max_depth": 5},
    {"n_estimators": 200, "max_depth": 10},
]

for config in experiment_runs:
    params = {**config, "min_samples_leaf": 4, "random_state": 42}
    run_name = f"rf-n{config['n_estimators']}-d{config['max_depth']}"

    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(params)
        mlflow.set_tag("dataset_version", DATASET_VERSION)

        model = RandomForestClassifier(**params)
        model.fit(X_train, y_train)
        preds = model.predict(X_test)

        mlflow.log_metrics({
            "accuracy": accuracy_score(y_test, preds),
            "f1":       f1_score(y_test, preds),
        })
        print(f"  {run_name}: accuracy={accuracy_score(y_test, preds):.4f}")

# %% [markdown]
# ## Query runs programmatically

# %%
# Pull all runs from this experiment into a DataFrame
runs_df = mlflow.search_runs(
    experiment_names=["01-tracking-basics-demo"],
    order_by=["metrics.f1 DESC"],
)

print(f"Total runs: {len(runs_df)}")
print(runs_df[["run_id", "params.n_estimators", "params.max_depth",
               "metrics.accuracy", "metrics.f1"]].head(10))

# %% [markdown]
# ## Load the best logged model
#
# In MLflow 3, models are first-class `LoggedModel` entities (separate from
# runs). `search_logged_models` is the canonical way to rank models by a
# linked metric — some runs may log metrics without logging a model, so
# searching runs and then loading a model can pick a model-less run. Ranking
# logged models directly avoids that.

# %%
best_models = mlflow.search_logged_models(
    experiment_ids=[runs_df.iloc[0]["experiment_id"]],
    order_by=[{"field_name": "metrics.f1", "ascending": False}],
    max_results=1,
)
if best_models.empty:
    raise RuntimeError("No LoggedModel in this experiment — run the training cells first.")

best = best_models.iloc[0]
model_uri = f"models:/{best['model_id']}"
print(f"Best model_id: {best['model_id']}")
print(f"Source run:    {best['source_run_id']}")
print(f"Loading from:  {model_uri}")

best_model = mlflow.sklearn.load_model(model_uri)
print(f"Loaded model:  {best_model}")
