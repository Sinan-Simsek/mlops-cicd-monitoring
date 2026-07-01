# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A multi-endpoint FastAPI ML-serving app that is the deployment target of a full
GitOps/observability tutorial (see `README.md`, a from-zero walkthrough). The
app itself is small; most of the repo's value is the surrounding
CI/CD → Docker → Kubernetes → ArgoCD → Prometheus/Grafana pipeline.

## Commands

```bash
# Install deps (uv is the project manager; uv.lock is committed)
uv sync                      # or: pip install -r requirements.txt

# Run the API locally (needs SQLALCHEMY_DATABASE_URL set — see caveat below)
uvicorn main:app --reload    # Swagger UI at http://localhost:8000/docs

# Lint (this is the ONLY check CI runs — there is no test suite)
ruff check .

# Regenerate the model pickles (downloads CSVs from the internet)
python train_iris_model.py
python train_advertising_model.py

# Build/smoke-test the container
docker build -t ml-prediction:dev .
```

There are **no automated tests** — CI's quality gate is `ruff check .` only.

## Critical caveats

- **Pickle paths are hardcoded to `/app/saved_models/...`** (absolute) in
  `routers/iris/iris_ep.py` and `routers/advertising/advertising_ep.py`, and
  they load **at import time**. The app therefore only imports cleanly inside
  the container (where code lives at `/app`). To run outside Docker you must
  either symlink `saved_models` to `/app/saved_models` or edit those paths.
- **A database connection is required to even start.** `database.py` reads
  `SQLALCHEMY_DATABASE_URL` (from `.env` via python-dotenv) and
  `main.py` calls `create_db_and_tables()` at startup. With no DB reachable,
  startup fails. Postgres is the intended backend (`k8s/postgres.yaml`).
- **The `/llm/chat` agent is built lazily**, so the app boots without an LLM
  key; only that endpoint 503s until a key is set. Do not move `build_agent()`
  to import time.
- **CI only fires for paths under `23_k8s_cicd_monitoring/**`**
  (`.github/workflows/ci.yaml`). This directory was extracted from a larger
  tutorial monorepo; the workflow still `cd`s into that subfolder. If this repo
  is now standalone, those `working-directory`/`paths`/`context` references are
  stale and the workflow will not run against the repo root.

## Architecture

**Request → prediction → persist.** Every endpoint follows the same shape:
validate input with a `SQLModel` request model, run inference, write a row via
a `Session` injected by `Depends(get_db)`, and return the persisted row. The DB
session generator lives in `database.py` (`get_db` yields then closes).

**Three routers, registered in `main.py`:**
- `routers/iris/` — KNN classifier + a `LabelEncoder`; returns the species.
- `routers/advertising/` — RandomForest regressor; returns predicted sales.
- `routers/llm/` — a LangChain agent (`create_agent` + `ToolStrategy`) that
  returns **structured** `ProductReview` output for a product-review string.

**Two model layers, don't confuse them:**
- Sklearn `.pkl` estimators in `saved_models/` (produced by the `train_*.py`
  scripts) — the actual ML models, loaded with `joblib`.
- `SQLModel` classes (in `models.py` and inline in each router) — request,
  response, and `table=True` DB-table schemas. `main.py` creates all tables
  at startup, so routers must **not** call `create_db_and_tables()` themselves.

**LLM provider switch** (`routers/llm/llm_ep.py`): `LLM_PROVIDER` env var
selects `google_genai` (Gemini, default) or `openrouter` (OpenAI-compatible).
Keys/model are read from env: `GOOGLE_API_KEY`/`GEMINI_MODEL` or
`OPENROUTER_API_KEY`/`OPENROUTER_MODEL`.

**Observability:** `main.py` mounts `prometheus-fastapi-instrumentator` to
expose `/metrics`; `/healthz` backs the k8s liveness/readiness probes.

## Deployment pipeline (how a code change reaches the cluster)

1. Push to `main` → GitHub Actions (`.github/workflows/ci.yaml`):
   `lint` → `build-and-push` (image to GHCR) → `update-manifest`.
2. `update-manifest` rewrites the image tag in
   `k8s/ml-prediction-deployment.yaml` to the new short SHA and commits it back
   with `[skip ci]` (so the commit doesn't re-trigger CI).
3. **ArgoCD** (`argocd/ml-prediction-app.yaml`) watches only the `k8s/` folder,
   sees the tag change, and rolls the Deployment (`prune` + `selfHeal` on).
4. **Prometheus Operator** scrapes the app via
   `k8s/ml-prediction-servicemonitor.yaml` (needs the `release:` label to match
   your kube-prometheus-stack install).

Note: `README.md`/CI push to **Docker Hub** in some places and **GHCR** in
others — the two are inconsistent; check which registry the manifest and
workflow actually reference before assuming.

## Conventions

- `ruff.toml` ignores `E402` (imports not at top) **only** in the `train_*.py`
  scripts, which import libraries right where they're used. Elsewhere keep
  imports at the top.
- Some code comments and the DB URL in `k8s/ml-prediction-deployment.yaml`
  contain a hardcoded demo password (`train:Ankara06`) and Turkish-language
  comments — tutorial artifacts, not secrets to preserve.
- `session_log.md` and `.env` are gitignored (local-only).
