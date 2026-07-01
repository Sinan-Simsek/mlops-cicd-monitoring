from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from database import create_db_and_tables
from routers.iris import iris_ep
from routers.advertising import advertising_ep
from routers.llm import llm_ep

app = FastAPI(title="Deploy ML/AI with API")

# Create all tables once at startup. The routers no longer do this themselves.
create_db_and_tables()

app.include_router(iris_ep.router)
app.include_router(advertising_ep.router)
app.include_router(llm_ep.router)

# Expose Prometheus metrics at /metrics — request counts, latencies,
# error rates, plus Python process / GC stats. Prometheus scrapes this
# endpoint via the ServiceMonitor in k8s/ml-prediction-servicemonitor.yaml.
Instrumentator().instrument(app).expose(app)


@app.get("/")
async def root():
    return {"message": "Hello FastAPI!"}


@app.get("/healthz")
async def healthz():
    """Liveness + readiness probe target.

    Returns 200 once the process can handle requests. Kubernetes calls
    this every few seconds (see livenessProbe / readinessProbe in
    k8s/ml-prediction-deployment.yaml) to decide whether to keep the
    pod in the Service's endpoints list and whether to restart it.
    """
    return {"status": "ok"}
