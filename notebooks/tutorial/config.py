# %% [markdown]
# # Shared Configuration (`config.py`)
#
# Importable module — Python can't import from filenames starting with a digit,
# so this is just `config.py` (treated as tutorial step 0).
#
# Minimal MLflow setup used by the rest of the tutorial templates.
#
# **Prerequisite — uv venv:** these templates expect the project venv at
# `/home/rami/Work/kyper/.venv` (Python 3.12). If it doesn't exist yet:
#
# ```bash
# cd /home/rami/Work/kyper
# uv venv --python 3.12 .venv
# source .venv/bin/activate
# uv pip install 'numpy<2' 'pandas<2.2' 'ray[default,tune]==2.41.0' \
#   mlflow pyod scikit-learn matplotlib xgboost statsmodels pyyaml \
#   ipywidgets jupyterlab jupytext pyarrow requests
# ```
#
# In VS Code/JupyterLab, select `/home/rami/Work/kyper/.venv/bin/python` as
# the kernel. See the tutorial [`README.md`](README.md) and the repo-root
# [`README.md`](../../README.md) for the full recipe.
#
# Two ways to run:
# - **Local file store** — leave `MLFLOW_TRACKING_URI` unset; runs land in
#   `kyper-framework/notebooks/tutorial/mlruns/` (anchored next to this file
#   regardless of the notebook's cwd).
# - **Cluster MLflow** — `kubectl port-forward -n ds-platform svc/mlflow 5000:5000`
#   then `export MLFLOW_TRACKING_URI=http://localhost:5000`.
#
# See the repo-root README ("Local development") for the full uv + port-forward flow.
#
# **Viewing the MLflow UI**
# Logging runs is decoupled from the UI — you need to start the UI separately:
#
# - **Local file store** — point the UI at the anchored tutorial mlruns:
#   ```bash
#   mlflow ui \
#     --backend-store-uri file:///home/rami/Work/kyper/kyper-framework/notebooks/tutorial/mlruns \
#     --port 5000
#   ```
#   Then open http://localhost:5000.
#   (Or from the tutorial dir: `cd kyper-framework/notebooks/tutorial &&
#   mlflow ui --backend-store-uri ./mlruns --port 5000`.)
# - **Cluster MLflow** — keep the `kubectl port-forward` above running;
#   the UI is already served at http://localhost:5000 by the cluster
#   MLflow pod (no local `mlflow ui` needed).
#
# > **IMPORTANT — pick ONE tracking backend per project and stick with it.**
# > Do NOT run some notebooks against local `./mlruns` and others against
# > the cluster MLflow. Experiments, runs, and registered models are
# > stored separately in each backend — mixing them fragments your run
# > history and makes `search_runs` / model registry lookups miss data.
# > Either work entirely locally (file store) OR entirely against the
# > cluster MLflow (port-forwarded); export `MLFLOW_TRACKING_URI` once
# > and keep it consistent for the whole session.

# %%
# ─────────────────────────────────────────────
# PROJECT SETTINGS — edit these
# ─────────────────────────────────────────────
EXPERIMENT_NAME = "my-experiment"   # one per project/objective

# ─────────────────────────────────────────────
# DO NOT EDIT BELOW THIS LINE
# ─────────────────────────────────────────────

# %%
import os
import threading
from pathlib import Path
import mlflow

# Default local file store when MLFLOW_TRACKING_URI is unset.
# Anchored to this file's directory so every notebook (and VS Code kernel,
# regardless of cwd) writes to the SAME ./tutorial/mlruns store — avoids
# ending up with mlruns/ split across several parent dirs.
_TUTORIAL_DIR = Path(__file__).resolve().parent
DEFAULT_LOCAL_URI = (_TUTORIAL_DIR / "mlruns").as_uri()   # file:///.../tutorial/mlruns

# ── Module-level guard — prevents double-init in the same process ─────────
_init_lock    = threading.Lock()
_initialized  = False
_current_experiment: str | None = None


def init_mlflow(experiment_name: str = EXPERIMENT_NAME) -> str:
    """
    One-call MLflow setup for kyper-framework tutorials.

    Resolves the tracking URI from `MLFLOW_TRACKING_URI` (falling back to
    the local file store `./mlruns`), sets it on the MLflow client, and
    activates `experiment_name` — creating it if it doesn't exist.

    Call this once at the top of each notebook. Idempotent: subsequent
    calls with the same experiment are no-ops; a different experiment
    triggers a clean reinit. Any dangling active run is closed before
    switching. Thread-safe (guarded by a module-level lock) so it's
    safe to call from Ray workers or concurrent notebook cells.

    Typical usage:
        from config import init_mlflow
        init_mlflow("my-experiment")
        with mlflow.start_run(run_name="..."):
            ...

    Returns the resolved tracking URI (useful for passing to
    Ray callbacks like `MLflowLoggerCallback(tracking_uri=...)`).
    """
    global _initialized, _current_experiment

    with _init_lock:
        if _initialized and _current_experiment == experiment_name:
            print(f"ℹ️  Already initialised — skipping "
                  f"(experiment: '{experiment_name}')")
            return mlflow.get_tracking_uri()

        if _initialized and _current_experiment != experiment_name:
            print(f"⚠️  Experiment changed: "
                  f"'{_current_experiment}' → '{experiment_name}'. Reinitialising.")

        active = mlflow.active_run()
        if active is not None:
            print(f"⚠️  Closing stale active run: {active.info.run_id}")
            mlflow.end_run()

        tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", DEFAULT_LOCAL_URI)

        # ── Self-heal: prune a stray nested ./mlruns/mlruns/ directory ────
        # MLflow treats every subdirectory of the tracking root as an
        # experiment and will crash on `set_experiment` if one lacks a
        # meta.yaml. A nested `mlruns/mlruns/` can appear if a Ray worker
        # (or a prior run) ever initialised MLflow with a *relative*
        # `./mlruns` URI while its cwd was already inside `mlruns/`.
        # No legitimate experiment is ever literally named "mlruns", so
        # any such directory is safe to remove.
        if tracking_uri.startswith("file://"):
            import shutil
            from urllib.parse import urlparse
            root = Path(urlparse(tracking_uri).path)
            stray = root / "mlruns"
            if stray.is_dir() and not (stray / "meta.yaml").exists():
                print(f"⚠️  Pruning stray nested MLflow dir: {stray}")
                shutil.rmtree(stray)

        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)

        _initialized        = True
        _current_experiment = experiment_name

        print(f"✅ MLflow tracking URI: {tracking_uri}")
        print(f"✅ MLflow experiment:   {experiment_name}")
        return tracking_uri


def reset_mlflow() -> None:
    """
    Reset the initialisation state so init_mlflow() can be called again.

    Useful in notebooks when:
      - You want to switch to a different experiment mid-session
      - A cell failed and left an active run open
      - You're re-running setup cells from scratch
    """
    global _initialized, _current_experiment

    with _init_lock:
        active = mlflow.active_run()
        if active is not None:
            print(f"⚠️  Closing stale active run: {active.info.run_id}")
            mlflow.end_run()

        _initialized        = False
        _current_experiment = None
        print("✅ MLflow state reset — safe to call init_mlflow() again.")


# %%
if __name__ == "__main__":
    init_mlflow()
    print("\nCalling init_mlflow() a second time (should be a no-op):")
    init_mlflow()
    print("\nConfig OK — you're ready to run the templates.")
