# %% [markdown]
# # 02 — Hyperparameter Search with Ray Tune
#
# **What you'll learn:**
# - Run parallel HPO trials with Ray Tune
# - Automatically log every trial to MLflow via `MLflowLoggerCallback`
# - Use search algorithms (Optuna, random) and schedulers (ASHA)
# - Retrieve the best config and re-train a final model
#
# **Runtime:** Local Ray (dev) or in-cluster Ray (via kyper-framework JupyterHub)

# %% [markdown]
# ## Setup

# %%
import ray
from ray import tune
from ray.air.integrations.mlflow import MLflowLoggerCallback

import mlflow
import numpy as np
from sklearn.datasets import make_classification
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.metrics import f1_score

# Make sibling config.py importable (VS Code or Jupyter, any cwd)
import sys
from pathlib import Path
try:
    _HERE = Path(__vsc_ipynb_file__).parent
except NameError:
    _HERE = Path.cwd()
sys.path.insert(0, str(_HERE))

from config import init_mlflow

EXPERIMENT_NAME = "02-ray-tune-hpo"
init_mlflow(experiment_name=EXPERIMENT_NAME)

TRACKING_URI = mlflow.get_tracking_uri()

# %% [markdown]
# ## Connect to Ray
#
# `ray.init()` with no address spawns a local Ray instance. In JupyterHub
# singleuser pods, `RAY_ADDRESS` is pre-set to the per-user RayCluster,
# and `ray.init()` picks it up automatically.

# %%
_rc = ray.init(ignore_reinit_error=True)
print(f"✅ Ray initialised — dashboard: {_rc.dashboard_url}")

# %% [markdown]
# ## Define the trainable function
#
# This is the function Ray Tune will call for each trial.
# Each call receives one combination of hyperparameters in `config`.

# %%
def train_model(config: dict) -> None:
    """
    Trainable function for Ray Tune.
    - Receives one hyperparameter combination in `config`
    - Reports metrics back to Tune via `tune.report()`
    - MLflow logging is handled automatically by MLflowLoggerCallback
    """
    # Recreate data inside the worker (workers don't share memory)
    X, y = make_classification(
        n_samples=5_000, n_features=20, n_informative=10, random_state=42
    )
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    # Build model from config. Search space is flat (RF and GB params
    # share one dict), so filter to the kwargs valid for the chosen model.
    # Use .get (not .pop): on local Ray the worker shares the same dict
    # object Tune stored for Result.config, and .pop would erase the key
    # from best_result.config after the run.
    model_type = config.get("model_type", "rf")

    RF_KEYS = {"n_estimators", "max_depth", "min_samples_leaf"}
    GB_KEYS = {"n_estimators", "max_depth", "learning_rate", "subsample"}

    if model_type == "rf":
        kwargs = {k: v for k, v in config.items() if k in RF_KEYS}
        model = RandomForestClassifier(**kwargs, random_state=42, n_jobs=-1)
    else:
        kwargs = {k: v for k, v in config.items() if k in GB_KEYS}
        model = GradientBoostingClassifier(**kwargs, random_state=42)

    # Cross-validated F1 (more reliable than a single split)
    cv_f1 = cross_val_score(model, X_train, y_train, cv=3, scoring="f1").mean()

    # Final fit on full train split for test evaluation
    model.fit(X_train, y_train)
    test_f1 = f1_score(y_test, model.predict(X_test))

    # ── Report metrics back to Tune ──────────────────────────────────────
    # MLflowLoggerCallback will automatically log these to MLflow
    tune.report({"cv_f1": cv_f1, "test_f1": test_f1})


# %% [markdown]
# ## Define search space
#
# `tune.choice`, `tune.loguniform`, `tune.randint` etc.
# See: https://docs.ray.io/en/latest/tune/api/search_space.html

# %%
search_space = {
    "model_type":       tune.choice(["rf", "gb"]),
    # Random Forest params
    "n_estimators":     tune.choice([50, 100, 200, 300]),
    "max_depth":        tune.choice([3, 5, 8, 10, None]),
    "min_samples_leaf": tune.randint(1, 10),
    # Gradient Boosting params (ignored when model_type="rf")
    "learning_rate":    tune.loguniform(1e-3, 0.3),
    "subsample":        tune.uniform(0.6, 1.0),
}

# %% [markdown]
# ## Run the hyperparameter search
#
# `MLflowLoggerCallback` logs every trial as a separate MLflow run
# nested under the parent experiment — no manual `mlflow.start_run()` needed.

# %%
tuner = tune.Tuner(
    # ── Resource allocation per trial ────────────────────────────────────
    tune.with_resources(train_model, resources={"cpu": 2}),

    param_space=search_space,

    run_config=tune.RunConfig(
        name="hpo-run",
        callbacks=[
            MLflowLoggerCallback(
                tracking_uri=TRACKING_URI,
                experiment_name=EXPERIMENT_NAME,
                save_artifact=False,   # set True to save model artifacts per trial
                tags={"owner": "your-name", "env": "dev"},
            )
        ],
    ),

    tune_config=tune.TuneConfig(
        metric="cv_f1",
        mode="max",
        num_samples=20,           # total trials to run
        max_concurrent_trials=4,  # parallel workers
    ),
)

results = tuner.fit()

# %% [markdown]
# ## Inspect results

# %%
best_result = results.get_best_result(metric="cv_f1", mode="max")

print("=" * 50)
print(f"Best config:   {best_result.config}")
print(f"Best CV F1:    {best_result.metrics['cv_f1']:.4f}")
print(f"Best test F1:  {best_result.metrics['test_f1']:.4f}")

# Full results table
results_df = results.get_dataframe()
print(f"\nAll {len(results_df)} trials:")
print(
    results_df[["config/model_type", "config/n_estimators",
                "cv_f1", "test_f1"]]
    .sort_values("cv_f1", ascending=False)
    .head(10)
)

# %% [markdown]
# ## Re-train final model with best config and log to MLflow

# %%
best_config = best_result.config.copy()
model_type  = best_config.pop("model_type")

X, y = make_classification(
    n_samples=5_000, n_features=20, n_informative=10, random_state=42
)
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

with mlflow.start_run(run_name="final-best-model") as run:
    mlflow.set_tags({
        "owner": "your-name",
        "stage": "final",
        "selected_from": "ray-tune-hpo",
    })
    RF_KEYS = {"n_estimators", "max_depth", "min_samples_leaf"}
    GB_KEYS = {"n_estimators", "max_depth", "learning_rate", "subsample"}
    kwargs = {
        k: v for k, v in best_config.items()
        if k in (RF_KEYS if model_type == "rf" else GB_KEYS)
    }
    mlflow.log_params({"model_type": model_type, **kwargs})

    if model_type == "rf":
        model = RandomForestClassifier(**kwargs, random_state=42, n_jobs=-1)
    else:
        model = GradientBoostingClassifier(**kwargs, random_state=42)

    model.fit(X_train, y_train)
    f1 = f1_score(y_test, model.predict(X_test))
    mlflow.log_metric("test_f1", f1)

    model_info = mlflow.sklearn.log_model(model, name="model")
    mlflow.set_tag("model_uri", model_info.model_uri)

    print(f"✅ Final model logged. Run ID: {run.info.run_id}")
    print(f"   model_uri:  {model_info.model_uri}")
    print(f"   Test F1: {f1:.4f}")
    FINAL_RUN_ID = run.info.run_id   # used in template 04

# %%
ray.shutdown()
