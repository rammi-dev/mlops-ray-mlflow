# %% [markdown]
# # 06 — Serving an MLflow Model Locally
#
# **What you'll learn:**
# - Log a sklearn model with a **signature** and **input_example**
# - Load it back in-process with `mlflow.pyfunc.load_model`
# - Launch MLflow's built-in REST server (`mlflow models serve`)
# - POST to `/invocations` from Python and parse the response
#
# The signature + input example are what MLflow uses to validate inputs
# at the REST boundary, so they're not optional for serving.

# %% [markdown]
# ## Setup

# %%
import mlflow
import numpy as np
import pandas as pd
from mlflow.models.signature import infer_signature
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

# Make sibling config.py importable (VS Code or Jupyter, any cwd)
import sys
from pathlib import Path
try:
    _HERE = Path(__vsc_ipynb_file__).parent
except NameError:
    _HERE = Path.cwd()
sys.path.insert(0, str(_HERE))

from config import init_mlflow

init_mlflow(experiment_name="06-mlflow-model-serve")

# %% [markdown]
# ## Train and log a model with signature + input_example

# %%
data = load_iris(as_frame=True)
X = data.data
y = data.target
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

model = RandomForestClassifier(n_estimators=50, random_state=42)
model.fit(X_train, y_train)

# Signature = input schema + output schema inferred from real examples
signature = infer_signature(X_train, model.predict(X_train))
input_example = X_train.head(3)

with mlflow.start_run(run_name="iris-rf-serve") as run:
    model_info = mlflow.sklearn.log_model(
        model,
        name="model",
        signature=signature,
        input_example=input_example,
    )
    RUN_ID = run.info.run_id
    MODEL_URI = model_info.model_uri   # e.g. models:/m-abc123…
    mlflow.set_tag("model_uri", MODEL_URI)
    print(f"✅ Logged {MODEL_URI}")

# %% [markdown]
# ## In-process inference via `mlflow.pyfunc`
#
# `pyfunc.load_model` returns a flavour-agnostic predictor — the same call
# works whether the underlying model is sklearn, xgboost, pytorch, etc.

# %%
pyfunc_model = mlflow.pyfunc.load_model(MODEL_URI)
predictions = pyfunc_model.predict(X_test.head(5))
print(f"In-process predictions: {predictions}")

# %% [markdown]
# ## Launch the REST server
#
# In a separate terminal, run:
#
# ```bash
# mlflow models serve -m <MODEL_URI> -p 5001 --no-conda
# ```
#
# Substitute the `MODEL_URI` printed above (e.g. `models:/m-abc123…`). `--no-conda` skips env recreation
# and runs in the current interpreter — fastest for local iteration.
#
# The server exposes `POST /invocations` accepting MLflow's JSON input
# formats (`dataframe_split`, `dataframe_records`, `instances`, `inputs`).

# %% [markdown]
# ## POST to the server from Python

# %%
import requests
import json

# `dataframe_split` is the most explicit format
payload = {
    "dataframe_split": {
        "columns": X_test.columns.tolist(),
        "data":    X_test.head(5).values.tolist(),
    }
}

# Uncomment once the server is running:
# response = requests.post(
#     "http://localhost:5001/invocations",
#     headers={"Content-Type": "application/json"},
#     data=json.dumps(payload),
#     timeout=10,
# )
# response.raise_for_status()
# print(f"REST predictions: {response.json()['predictions']}")

print("Payload ready:")
print(json.dumps(payload, indent=2)[:400], "...")

# %% [markdown]
# ## Next step: containerise with `mlflow models build-docker`
#
# `mlflow models build-docker -m <MODEL_URI> -n iris-rf:latest`
# produces an image serving the same `/invocations` API — ship it anywhere
# that runs containers (k8s Deployment, Cloud Run, etc.).
