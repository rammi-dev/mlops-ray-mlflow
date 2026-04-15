# %% [markdown]
# # 04 — Model Registry with Aliases
#
# **What you'll learn:**
# - Register a model in the MLflow Model Registry
# - Use **aliases** (`@champion`, `@challenger`) for lifecycle management —
#   the modern replacement for the deprecated `Staging`/`Production` stages
# - Load a registered model by alias and predict
#
# **Prerequisite:** Run template 01, 02, or 03 first to have a logged model.
# Set `SOURCE_RUN_ID` below to a run ID that has a logged model artifact.
#
# **Note:** MLflow 2.9+ deprecated model stages in favour of aliases and tags.
# Aliases are mutable named pointers to a specific version (e.g. `@champion`
# always resolves to whatever version is currently blessed).

# %% [markdown]
# ## Setup

# %%
import mlflow
from mlflow import MlflowClient
import numpy as np

# Make sibling config.py importable (VS Code or Jupyter, any cwd)
import sys
from pathlib import Path
try:
    _HERE = Path(__vsc_ipynb_file__).parent
except NameError:
    _HERE = Path.cwd()
sys.path.insert(0, str(_HERE))

from config import init_mlflow

EXPERIMENT_NAME = "04-registry-demo"
init_mlflow(experiment_name=EXPERIMENT_NAME)

# ── Set this to a run ID from a previous template ────────────────────────
# MLflow 3: paste the MODEL_URI printed by template 01/02 (e.g. "models:/m-abc…")
# OR the run_id — we'll resolve it via the "model_uri" tag set by template 01.
SOURCE_RUN_ID = "PASTE_RUN_ID_HERE"
REGISTERED_MODEL_NAME = "churn-classifier"

client = MlflowClient()

# %% [markdown]
# ## Step 1 — Register the model
#
# This creates (or updates) a named model in the MLflow registry,
# versioned automatically.

# %%
# Resolve the model URI from the source run.
# - Preferred: the `model_uri` tag set by template 01 at log time
#   (`mlflow.set_tag("model_uri", model_info.model_uri)`).
# - Fallback: the canonical `runs:/<run_id>/<artifact_path>` form, which
#   works for any run that logged a model under `name="model"` (e.g.
#   template 02's final-best-model cell).
source_run = client.get_run(SOURCE_RUN_ID)
model_uri = source_run.data.tags.get("model_uri") or f"runs:/{SOURCE_RUN_ID}/model"
print(f"Registering from: {model_uri}")

registered = mlflow.register_model(
    model_uri=model_uri,
    name=REGISTERED_MODEL_NAME,
)

version = registered.version
print(f"✅ Registered '{REGISTERED_MODEL_NAME}' — version {version}")

# %% [markdown]
# ## Step 2 — Add description and tags

# %%
client.update_model_version(
    name=REGISTERED_MODEL_NAME,
    version=version,
    description="Random Forest trained on synthetic churn data. "
                "Optimised for F1 score.",
)

client.set_model_version_tag(
    name=REGISTERED_MODEL_NAME,
    version=version,
    key="dataset_version",
    value="synthetic-v1",
)
client.set_model_version_tag(
    name=REGISTERED_MODEL_NAME,
    version=version,
    key="framework",
    value="scikit-learn",
)

print(f"✅ Tags and description set for version {version}")

# %% [markdown]
# ## Step 3 — Assign the `@challenger` alias
#
# Aliases are mutable pointers. Use `@challenger` for a version under
# evaluation and `@champion` for the live production version.

# %%
client.set_registered_model_alias(
    name=REGISTERED_MODEL_NAME,
    alias="challenger",
    version=version,
)
print(f"✅ {REGISTERED_MODEL_NAME}@challenger → version {version}")

# %% [markdown]
# ## Step 4 — Validate the challenger
#
# Load the aliased model and validate on a holdout set before promotion.

# %%
challenger = mlflow.sklearn.load_model(
    f"models:/{REGISTERED_MODEL_NAME}@challenger"
)

# Quick sanity check — replace with your real validation data
X_holdout = np.random.randn(100, 20)
preds = challenger.predict(X_holdout)
assert preds.shape == (100,), "Prediction shape mismatch"
print(f"✅ Challenger validation passed. Sample predictions: {preds[:5]}")

with mlflow.start_run(run_name="challenger-validation"):
    mlflow.set_tags({
        "stage": "challenger-validation",
        "model_name": REGISTERED_MODEL_NAME,
        "model_version": version,
    })
    mlflow.log_metric("n_validation_samples", 100)
    mlflow.log_metric("validation_passed", 1)

# %% [markdown]
# ## Step 5 — Promote challenger to champion
#
# Reassigning the alias is atomic — no need to archive the previous version.

# %%
client.set_registered_model_alias(
    name=REGISTERED_MODEL_NAME,
    alias="champion",
    version=version,
)

# Remove the challenger alias now that it's the champion
client.delete_registered_model_alias(
    name=REGISTERED_MODEL_NAME,
    alias="challenger",
)

print(f"✅ {REGISTERED_MODEL_NAME}@champion → version {version}")

# %% [markdown]
# ## Step 6 — Load the champion and predict

# %%
champion = mlflow.sklearn.load_model(
    f"models:/{REGISTERED_MODEL_NAME}@champion"
)

X_sample = np.random.randn(5, 20)
predictions = champion.predict(X_sample)
print(f"Champion predictions: {predictions}")

# %% [markdown]
# ## Step 7 — Inspect aliases and versions

# %%
mv = client.get_model_version_by_alias(REGISTERED_MODEL_NAME, "champion")
print(f"Champion is version {mv.version}")
print(f"  run_id: {mv.run_id}")
print(f"  tags:   {mv.tags}")

# All versions
versions = client.search_model_versions(f"name='{REGISTERED_MODEL_NAME}'")
for v in versions:
    print(f"  v{v.version}  aliases={v.aliases}  tags={dict(v.tags)}")

# %% [markdown]
# ## Alias lifecycle
#
# ```
# mlflow.register_model()
#         │
#         ▼
#     version N  ◄── set_registered_model_alias("challenger", N)
#         │              │
#         │   validate   │
#         ▼              ▼
#     set_registered_model_alias("champion", N)
#     delete_registered_model_alias("challenger")
# ```
#
# For serving, see template `06_mlflow_model_serve.py`.
