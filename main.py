import logging
import time

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.exc import OperationalError

from database import create_db_and_tables
from routers.iris import iris_ep
from routers.advertising import advertising_ep
from routers.llm import llm_ep

log = logging.getLogger("startup")

app = FastAPI(title="Deploy ML/AI with API")


def init_db_with_retry(retries: int = 30, delay: float = 2.0) -> None:
    """Create tables at startup, retrying while the database is unreachable.

    After a cluster restart the app pod can start before CoreDNS / Postgres
    are ready, so the first connection fails (e.g. OperationalError
    "could not translate host name 'postgres'"). Retry until the DB is
    reachable instead of crashing and leaning on Kubernetes to restart us.
    """
    for attempt in range(1, retries + 1):
        try:
            create_db_and_tables()
            log.info("Database ready (attempt %s).", attempt)
            return
        except OperationalError as exc:
            log.warning("DB not ready (attempt %s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    # Last try: if the DB is still unreachable, let the exception propagate so
    # the pod fails loudly rather than serving with no database.
    create_db_and_tables()


# Create all tables once at startup. The routers no longer do this themselves.
init_db_with_retry()

app.include_router(iris_ep.router)
app.include_router(advertising_ep.router)
app.include_router(llm_ep.router)

# Expose Prometheus metrics at /metrics — request counts, latencies,
# error rates, plus Python process / GC stats. Prometheus scrapes this
# endpoint via the ServiceMonitor in k8s/ml-prediction-servicemonitor.yaml.
Instrumentator().instrument(app).expose(app)


@app.get("/")
async def root():
    return {"message": "Hello from live FastAPI!"}


@app.get("/healthz")
async def healthz():
    """Liveness + readiness probe target.

    Returns 200 once the process can handle requests. Kubernetes calls
    this every few seconds (see livenessProbe / readinessProbe in
    k8s/ml-prediction-deployment.yaml) to decide whether to keep the
    pod in the Service's endpoints list and whether to restart it.
    """
    return {"status": "ok"}
