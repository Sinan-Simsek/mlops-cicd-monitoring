# K8s + GitOps + Observability — build from zero

A copy-paste tutorial that assumes you have **nothing**. By the end you'll
have a multi-endpoint FastAPI app deployed on **Docker Desktop Kubernetes**
with:

1. **GitHub Actions CI/CD** — push to `main`, image lands in Docker Hub,
   the k8s manifest tag is bumped and committed back automatically.
2. **ArgoCD (GitOps)** — watches the `k8s/` folder and continuously syncs
   it to the cluster.
3. **Prometheus + Grafana** — scrape the app's `/metrics` endpoint via a
   `ServiceMonitor`, visualise request rate / latency / errors.

```
                                  ┌─── GitHub repo (this folder) ───┐
                                  │  app code, Dockerfile, k8s/      │
                                  └──────────────┬───────────────────┘
                                                 │  push
                                                 ▼
                                    ┌────────────────────────┐
                                    │  GitHub Actions (CI)   │
                                    │  lint → build → push   │
                                    │     to Docker Hub      │
                                    │  then commit new tag   │
                                    └────────────┬───────────┘
                                                 │  manifest change
                                                 ▼
┌──── Docker Desktop Kubernetes ──────────────────────────────────────┐
│                                                                    │
│   ┌──────────┐  watches repo  ┌──────────┐  scrapes /metrics       │
│   │  ArgoCD  │ ─────────────► │ FastAPI  │ ◄─── ┌──────────────┐   │
│   │          │  syncs k8s/    │ (3 pods) │      │  Prometheus  │   │
│   └──────────┘                └────┬─────┘      │  (Operator)  │   │
│                                    │            └──────┬───────┘   │
│                                    ▼                   │           │
│                              ┌────────────┐            ▼           │
│                              │ PostgreSQL │      ┌──────────┐      │
│                              │  (PVC)     │      │ Grafana  │      │
│                              └────────────┘      └──────────┘      │
└────────────────────────────────────────────────────────────────────┘
```

---

## Step 0 — Tools you need on the host

| Tool | Install hint |
|---|---|
| Docker Desktop with **Kubernetes enabled** | Docker Desktop → Settings → Kubernetes → check **Enable Kubernetes** → Apply & Restart. Wait for the bottom-left indicator to turn green. |
| `kubectl` | Ships with Docker Desktop. Verify: `kubectl version --client` |
| `helm` | `brew install helm` (macOS), or download from [helm.sh](https://helm.sh/docs/intro/install/) |
| `git` + a **GitHub account** | `git --version` |
| Python 3.12 | for training the pickles locally |

Switch `kubectl` to the Docker Desktop cluster (idempotent):

```bash
kubectl config use-context docker-desktop
kubectl get nodes
# NAME             STATUS   ROLES           AGE   VERSION
# docker-desktop   Ready    control-plane   ...   v1.x.x
```

### Fix cluster DNS (WSL2 / Docker Desktop only — run this once per reset)

On Docker Desktop with the WSL2 backend, CoreDNS forwards to the host's
`/etc/resolv.conf`, which WSL2 fills in with its own internal forwarder
(`nameserver 10.255.255.254`). That forwarder is **not reachable from
inside cluster pods**, so any pod that tries to resolve a public
hostname — for example ArgoCD trying to clone
`github.com/<your-fork>` — fails with:

```
dial tcp: lookup github.com on 10.96.0.10:53: server misbehaving
```

Override CoreDNS to forward to public DNS instead:

```bash
kubectl -n kube-system apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns
  namespace: kube-system
data:
  Corefile: |
    .:53 {
        errors
        health {
            lameduck 5s
        }
        ready
        kubernetes cluster.local in-addr.arpa ip6.arpa {
            pods insecure
            fallthrough in-addr.arpa ip6.arpa
            ttl 30
        }
        prometheus :9153
        forward . 8.8.8.8 1.1.1.1 {
            max_concurrent 1000
        }
        cache 30
        loop
        reload
        loadbalance
    }
EOF

kubectl -n kube-system rollout restart deployment coredns
kubectl -n kube-system rollout status deployment coredns
```

Verify a pod can now resolve a public name:

```bash
kubectl run -it --rm dnstest --image=busybox:1.36 --restart=Never -- nslookup github.com
# Name:      github.com
# Address 1: 140.82.121.4 lb-140-82-121-4-fra.github.com
```

> **Heads up — this patch is wiped by a cluster reset.** Every time you
> use **Docker Desktop → Settings → Kubernetes → Reset Kubernetes
> Cluster**, the CoreDNS ConfigMap reverts to the default and you'll
> hit the same DNS error again. Re-apply this block, or set
> **Docker Desktop → Settings → Resources → Network → Manual DNS
> configuration** to `8.8.8.8` for a host-level fix that survives
> resets.

---

## Step 1 — Create the project skeleton

Pick any name you like — this guide uses `mlops-cicd-monitoring`.

```bash
mkdir -p mlops-cicd-monitoring/{app,saved_models,k8s,argocd,.github/workflows,routers/iris,routers/advertising,routers/llm}
cd mlops-cicd-monitoring
```

Empty package markers so Python recognises the router subdirectories:

```bash
touch routers/__init__.py \
      routers/iris/__init__.py \
      routers/advertising/__init__.py \
      routers/llm/__init__.py
```

Sanity:

```bash
tree -L 2 -a
# .
# ├── .github
# │   └── workflows
# ├── argocd
# ├── k8s
# ├── routers
# │   ├── __init__.py
# │   ├── advertising
# │   ├── iris
# │   └── llm
# ├── app
# └── saved_models
```

(No `tree`? `find . -maxdepth 2` does the same.)

---

## Step 2 — `requirements.txt`

Create file **`requirements.txt`** and paste:

```
fastapi[all]==0.136.1
uvicorn[standard]==0.46.0
pandas== 2.2.3
scikit-learn==1.5.2
sqlmodel>=0.0.38
psycopg2-binary>=2.9.12
python-dotenv>=1.2.2
langchain
langchain-google-genai
joblib==1.4.2
langchain-community>=0.4.1
prometheus-fastapi-instrumentator>=7.0
```

---

## Step 3 — `.gitignore`

Create file **`.gitignore`** and paste:

```
# Python-generated files
__pycache__/
*.py[oc]
build/
dist/
wheels/
*.egg-info

# Virtual environments
.venv

# Local secrets. Copy .env.example to .env for local/Compose runs and the
# app-secret Secret (Step 19); .env holds real keys so it must never be
# committed. In k8s, env vars come from ml-prediction-deployment.yaml plus
# that Secret.
.env
```

---

## Step 4 — App code

Create file **`main.py`** and paste:

```python
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
    """Liveness + readiness probe target for the K8s pod spec."""
    return {"status": "ok"}
```

Create file **`database.py`** and paste:

```python
import os
from dotenv import load_dotenv
from sqlmodel import create_engine, SQLModel, Session

load_dotenv()
SQLALCHEMY_DATABASE_URL = os.getenv("SQLALCHEMY_DATABASE_URL")

engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()
```

Create file **`models.py`** and paste:

```python
from datetime import datetime, timezone
from typing import Optional, Literal
from sqlmodel import SQLModel, Field, text


class RawProductReview(SQLModel):
    user: str = Field(..., description="Reviewer username")
    product: str = Field(..., description="Product name")
    review: str = Field(..., description="Full review text")


class ProductReview(SQLModel):
    """LLM analysis schema (the structured response the model returns)."""
    rating: int = Field(ge=1, le=5)
    sentiment: Literal["positive", "negative", "neutral", "mixed"]
    confidence: float = Field(ge=0.0, le=1.0)
    language: str = Field(min_length=2, max_length=2)
    key_points: list[str]


class ProductReviewRateResult(SQLModel, table=True):
    """What we persist + return for /llm/chat."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_info: str
    review: str
    product: str = Field(index=True)
    rate: Optional[int] = None
    sentiment: Optional[str] = Field(default=None, index=True)
    confidence: Optional[float] = None
    language: Optional[str] = Field(default=None, index=True)
    key_points: Optional[str] = None
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")},
    )
```

---

## Step 5 — Routers

Create file **`routers/iris/iris_ep.py`** and paste:

```python
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, status, Depends
from sqlmodel import Session, SQLModel, Field, text
import joblib

from database import get_db

router = APIRouter()


class IrisPredictionModel(SQLModel):
    SepalLengthCm: float
    SepalWidthCm: float
    PetalLengthCm: float
    PetalWidthCm: float


class IrisResponse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    SepalLengthCm: float = None
    SepalWidthCm: float = None
    PetalLengthCm: float = None
    PetalWidthCm: float = None
    PredictedSpecie: str = None
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")},
    )


classifier_loaded = joblib.load("/app/saved_models/01.knn_with_iris_dataset.pkl")
encoder_loaded = joblib.load("/app/saved_models/02.iris_label_encoder.pkl")


def make_prediction(request):
    features = [list(request.model_dump().values())]
    prediction_raw = classifier_loaded.predict(features)
    prediction_real = encoder_loaded.inverse_transform(prediction_raw)
    return prediction_real[0]


@router.post("/prediction/iris", status_code=status.HTTP_201_CREATED)
async def predict_iris(request: IrisPredictionModel, session: Session = Depends(get_db)):
    prediction = make_prediction(request)
    new_predicted_iris = IrisResponse(
        SepalLengthCm=request.SepalLengthCm,
        SepalWidthCm=request.SepalWidthCm,
        PetalLengthCm=request.PetalLengthCm,
        PetalWidthCm=request.PetalWidthCm,
        PredictedSpecie=prediction,
    )
    with session:
        session.add(new_predicted_iris)
        session.commit()
        session.refresh(new_predicted_iris)
    return new_predicted_iris
```

Create file **`routers/advertising/advertising_ep.py`** and paste:

```python
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, status, Depends
from sqlmodel import Session, SQLModel, Field, text
import joblib

from database import get_db

router = APIRouter()


class Advertising(SQLModel):
    TV: float
    Radio: float
    Newspaper: float


class AdvertisingResponse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    TV: float = None
    Radio: float = None
    Newspaper: float = None
    PredictedSales: float = None
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")},
    )


estimator_loaded = joblib.load("/app/saved_models/03.randomforest_with_advertising.pkl")


def make_prediction(request):
    features = [list(request.model_dump().values())]
    prediction_raw = estimator_loaded.predict(features)
    return float(prediction_raw[0])


@router.post("/prediction/advertising", status_code=status.HTTP_201_CREATED)
async def predict_advertising(request: Advertising, session: Session = Depends(get_db)):
    prediction = make_prediction(request)
    new_predicted_adv = AdvertisingResponse(
        TV=request.TV,
        Radio=request.Radio,
        Newspaper=request.Newspaper,
        PredictedSales=prediction,
    )
    with session:
        session.add(new_predicted_adv)
        session.commit()
        session.refresh(new_predicted_adv)
    return new_predicted_adv
```

Create file **`routers/llm/llm_ep.py`** and paste:

```python
from fastapi import APIRouter, Depends, HTTPException, status
import json
import logging
import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from models import ProductReview, RawProductReview, ProductReviewRateResult
from database import get_db
from sqlmodel import Session

load_dotenv()


router = APIRouter()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("review_analysis")

SYSTEM_PROMPT = (
    "You analyze product reviews and return structured data.\n"
    "Sentiment values:\n"
    "  - 'positive': clearly favorable overall.\n"
    "  - 'negative': clearly unfavorable overall.\n"
    "  - 'neutral': purely factual / indifferent / no clear evaluation.\n"
    "  - 'mixed': contains both clearly positive and clearly negative points.\n"
    "Always include an honest 'confidence' (0-1). Lower it for short, "
    "ambiguous, sarcastic, or off-topic reviews. Detect 'language' as a "
    "two-letter ISO 639-1 code (e.g. 'en', 'tr', 'de')."
)

REQUIRED_FIELDS = ("user", "product", "review")

# Which LLM backend /llm/chat uses. "google_genai" (Gemini, default) or
# "openrouter" (any OpenRouter-hosted model via its OpenAI-compatible API).
# Set LLM_PROVIDER + the matching API key in .env (local) or the app-secret
# Secret (Kubernetes).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google_genai").strip().lower()

# Tuning shared across providers.
_MODEL_KWARGS = dict(temperature=0.1, timeout=30, max_tokens=500)


def build_model():
    if LLM_PROVIDER == "openrouter":
        return init_chat_model(
            os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            model_provider="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            **_MODEL_KWARGS,
        )
    if LLM_PROVIDER in ("google_genai", "gemini", "google"):
        return init_chat_model(
            os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            model_provider="google_genai",
            **_MODEL_KWARGS,
        )
    raise ValueError(
        f"Unknown LLM_PROVIDER {LLM_PROVIDER!r}; use 'google_genai' or 'openrouter'."
    )


def build_agent():
    return create_agent(
        model=build_model(),
        tools=[],
        response_format=ToolStrategy(schema=ProductReview),
        system_prompt=SYSTEM_PROMPT,
    )

def analyze_one(agent, review_text: str) -> ProductReview:
    result = agent.invoke({
        "messages": [{
            "role": "user",
            "content": f"Analyze this review: '{review_text}'",
        }]
    })
    response = result.get("structured_response")
    if response is None:
        raise RuntimeError("agent returned no structured_response")
    return response


# Build the agent lazily on first request, not at import time: it needs a
# Google API key, and we want the app (and its other endpoints) to start
# even when GOOGLE_API_KEY is unset — only /llm/chat should then error.
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


@router.post("/llm/chat", response_model=ProductReviewRateResult)
async def make_chat(request: RawProductReview, session: Session = Depends(get_db)):
    try:
        analysis = analyze_one(get_agent(), request.review)
    except Exception as e:
        log.error(e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM analysis failed (is GOOGLE_API_KEY set?): {e}",
        )

    product_review = ProductReviewRateResult(
                user_info = request.user,
                review = request.review,
                product = request.product,
                rate = analysis.rating,
                confidence = analysis.confidence,
                sentiment = analysis.sentiment,
                language = analysis.language,
                key_points = json.dumps(analysis.key_points)
                )

    with session:
        session.add(product_review) # Use session
        session.commit()
        session.refresh(product_review) # Route ends, get_db() resumes and closes session

    return  product_review
```

---

## Step 6 — Training scripts

Create file **`train_iris_model.py`** and paste:

```python
import joblib
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import accuracy_score

df = pd.read_csv("https://raw.githubusercontent.com/erkansirin78/datasets/master/iris.csv")
X = df.iloc[:, :-1].values
y = df.iloc[:, -1]

encoder = LabelEncoder()
y = encoder.fit_transform(y)
joblib.dump(encoder, "saved_models/02.iris_label_encoder.pkl")

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.33, random_state=42)
classifier = KNeighborsClassifier(n_neighbors=5)
classifier.fit(X_train, y_train)

y_pred = classifier.predict(X_test)
print(f"Accuracy: {accuracy_score(y_test, y_pred) * 100:.2f}%")

joblib.dump(classifier, "saved_models/01.knn_with_iris_dataset.pkl")
```

Create file **`train_advertising_model.py`** and paste:

```python
import joblib
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score

df = pd.read_csv("https://raw.githubusercontent.com/erkansirin78/datasets/master/Advertising.csv")
X = df.iloc[:, 1:-1].values
y = df.iloc[:, -1]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.33, random_state=42)
estimator = RandomForestRegressor(n_estimators=200)
estimator.fit(X_train, y_train)

print(f"R2: {r2_score(y_test, estimator.predict(X_test))}")

joblib.dump(estimator, "saved_models/03.randomforest_with_advertising.pkl")
```

Run them (they download the CSVs from the internet — needs network):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python train_iris_model.py
python train_advertising_model.py

ls -l saved_models/
# 01.knn_with_iris_dataset.pkl
# 02.iris_label_encoder.pkl
# 03.randomforest_with_advertising.pkl
```

---

## Step 7 — `Dockerfile`

Create file **`Dockerfile`** and paste:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Quick local smoke test — confirms the Dockerfile is healthy before
GitHub Actions tries to build the same thing:

```bash
docker build -t ml-prediction:dev .
docker images ml-prediction
# should list the dev tag
```

(We don't `docker run` it standalone because it needs a Postgres to
talk to. The end-to-end run happens in Kubernetes from Step 15
onward.)

---

## Step 8 — Kubernetes manifests

> **Before pasting the next file**, decide your Docker Hub username
> (e.g. `erkansirin78`) — it goes into the image reference. The text
> below uses `YOUR-DOCKERHUB-USER` as a placeholder; swap it inline.

Create file **`k8s/postgres.yaml`** and paste:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: postgres-secret
type: Opaque
stringData:
  POSTGRES_USER: train
  POSTGRES_PASSWORD: Ankara06
  POSTGRES_DB: traindb
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  labels:
    app: postgres
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:16
          ports:
            - name: postgres
              containerPort: 5432
          envFrom:
            - secretRef:
                name: postgres-secret
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "train", "-d", "traindb"]
            initialDelaySeconds: 5
            periodSeconds: 5
          livenessProbe:
            exec:
              command: ["pg_isready", "-U", "train", "-d", "traindb"]
            initialDelaySeconds: 20
            periodSeconds: 10
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
              subPath: pgdata
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: postgres-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  type: ClusterIP
  selector:
    app: postgres
  ports:
    - name: postgres
      port: 5432
      targetPort: 5432
```

Create file **`k8s/ml-prediction-deployment.yaml`** and paste — **replace
`YOUR-DOCKERHUB-USER` on the `image:` line with your own Docker Hub
username**:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ml-prediction-deployment
  labels:
    app: ml-prediction
spec:
  replicas: 3
  selector:
    matchLabels:
      app: ml-prediction
  template:
    metadata:
      labels:
        app: ml-prediction
    spec:
      containers:
      - name: ml-prediction
        # ↓↓↓  Replace YOUR-DOCKERHUB-USER with your Docker Hub username.
        image: docker.io/YOUR-DOCKERHUB-USER/ml-prediction:latest
        # IfNotPresent lets Docker Desktop's k8s reuse a locally-built
        # image of the same tag without forcing a registry pull.
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 8000
          name: http
        env:
        - name: SQLALCHEMY_DATABASE_URL
          value: "postgresql+psycopg2://train:Ankara06@postgres:5432/traindb"
        # LLM settings for /llm/chat — all optional (app boots without them,
        # only /llm/chat errors). LLM_PROVIDER picks the backend:
        # "google_genai" (default) or "openrouter".
        - name: LLM_PROVIDER
          valueFrom:
            secretKeyRef:
              name: app-secret
              key: LLM_PROVIDER
              optional: true
        - name: GOOGLE_API_KEY
          valueFrom:
            secretKeyRef:
              name: app-secret
              key: GOOGLE_API_KEY
              optional: true
        - name: OPENROUTER_API_KEY
          valueFrom:
            secretKeyRef:
              name: app-secret
              key: OPENROUTER_API_KEY
              optional: true
        readinessProbe:
          httpGet:
            path: /healthz
            port: http
          initialDelaySeconds: 5
          periodSeconds: 5
        livenessProbe:
          httpGet:
            path: /healthz
            port: http
          initialDelaySeconds: 15
          periodSeconds: 15
---
apiVersion: v1
kind: Service
metadata:
  name: ml-prediction
  labels:
    app: ml-prediction
spec:
  type: NodePort
  selector:
    app: ml-prediction
  ports:
  - port: 8000
    targetPort: http
    nodePort: 30080
    protocol: TCP
    name: http
```

Create file **`k8s/ml-prediction-servicemonitor.yaml`** and paste:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: ml-prediction
  labels:
    app: ml-prediction
    release: kube-prometheus-stack    # must match the helm release name in Step 16
spec:
  selector:
    matchLabels:
      app: ml-prediction
  namespaceSelector:
    matchNames:
      - default
  endpoints:
  - port: http
    path: /metrics
    interval: 15s
```

Create file **`k8s/ingress-ml-prediction.yaml`** and paste (only used if
you decide to install the NGINX ingress controller in Step 18):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ml-prediction-ingress
spec:
  ingressClassName: nginx
  rules:
    - host: ml.prediction.vbo.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ml-prediction
                port:
                  number: 8000
```

---

## Step 9 — ArgoCD Application manifest

Create file **`argocd/ml-prediction-app.yaml`** and paste — **replace
`YOUR-USER/YOUR-REPO`** on the `repoURL:` line:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ml-prediction
  namespace: argocd
spec:
  project: default

  source:
    # ↓↓↓  Replace with your fork's URL.
    repoURL: https://github.com/YOUR-USER/YOUR-REPO.git
    targetRevision: main
    path: k8s

  destination:
    server: https://kubernetes.default.svc
    namespace: default

  syncPolicy:
    automated:
      prune: true       # delete resources removed from the manifest
      selfHeal: true    # revert manual cluster edits back to the manifest
    syncOptions:
      - CreateNamespace=true
```

---

## Step 10 — GitHub Actions workflow

Create file **`.github/workflows/ci.yaml`** and paste (replace
`YOUR-DOCKERHUB-USER` in the `IMAGE` line with your Docker Hub username):

```yaml
name: CI/CD

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: write

env:
  IMAGE: docker.io/YOUR-DOCKERHUB-USER/ml-prediction

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install ruff
      - run: ruff check .

  build-and-push:
    runs-on: ubuntu-latest
    needs: lint
    if: github.ref == 'refs/heads/main'
    outputs:
      short_sha: ${{ steps.vars.outputs.short_sha }}
    steps:
      - uses: actions/checkout@v4

      - name: Compute short SHA
        id: vars
        run: echo "short_sha=$(echo ${{ github.sha }} | cut -c1-7)" >> "$GITHUB_OUTPUT"

      - uses: docker/setup-buildx-action@v3

      - name: Login to Docker Hub
        uses: docker/login-action@v3
        with:
          registry: docker.io
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: |
            ${{ env.IMAGE }}:latest
            ${{ env.IMAGE }}:${{ steps.vars.outputs.short_sha }}

  update-manifest:
    runs-on: ubuntu-latest
    needs: build-and-push
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Bump image tag in deployment manifest
        run: |
          SHA="${{ needs.build-and-push.outputs.short_sha }}"
          IMAGE="${{ env.IMAGE }}"
          sed -i -E "s#(image: ${IMAGE//\//\\/}):.*#\1:${SHA}#" k8s/ml-prediction-deployment.yaml
          git diff k8s/ml-prediction-deployment.yaml

      - name: Commit and push
        run: |
          if git diff --quiet k8s/ml-prediction-deployment.yaml; then
            echo "No tag change."
            exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add k8s/ml-prediction-deployment.yaml
          git commit -m "chore: bump image to ${{ needs.build-and-push.outputs.short_sha }} [skip ci]"
          git push
```

---

## Step 11 — Verify the local project tree

```bash
find . -type f -not -path './.venv/*' -not -path './saved_models/*' | sort
```

You should see something like:

```
./.github/workflows/ci.yaml
./.gitignore
./Dockerfile
./argocd/ml-prediction-app.yaml
./database.py
./k8s/ingress-ml-prediction.yaml
./k8s/ml-prediction-deployment.yaml
./k8s/ml-prediction-servicemonitor.yaml
./k8s/postgres.yaml
./main.py
./models.py
./requirements.txt
./routers/__init__.py
./routers/advertising/__init__.py
./routers/advertising/advertising_ep.py
./routers/iris/__init__.py
./routers/iris/iris_ep.py
./routers/llm/__init__.py
./routers/llm/llm_ep.py
./train_advertising_model.py
./train_iris_model.py
```

And:

```bash
ls saved_models/
# 01.knn_with_iris_dataset.pkl  02.iris_label_encoder.pkl  03.randomforest_with_advertising.pkl
```

---

## Step 12 — Create a GitHub repo

On GitHub: **+ → New repository**. Choose a name (e.g.
`mlops-cicd-monitoring`). Leave it **public**. Do **not** add a
README, .gitignore, or license — we already have these locally.

Initialise the local repo and push:

```bash
git init -b main
git add .
git commit -m "Initial: app + k8s + argocd + CI workflow"
git remote add origin https://github.com/YOUR-USER/YOUR-REPO.git
git push -u origin main
```

---

## Step 13 — Add Docker Hub secrets and allow Actions to commit back

**a) Docker Hub credentials.** `build-and-push` logs in to Docker Hub,
so it needs two repo secrets. Create a Docker Hub access token first
(Docker Hub → **Account Settings → Personal access tokens → Generate**,
**Read & Write** scope), then:

```bash
gh secret set DOCKERHUB_USERNAME --repo YOUR-USER/YOUR-REPO --body "your-dockerhub-username"
gh secret set DOCKERHUB_TOKEN    --repo YOUR-USER/YOUR-REPO   # paste the token when prompted
```

(Or via the UI: **Repo → Settings → Secrets and variables → Actions →
New repository secret**.)

**b) Write permission for the tag bump.** The `update-manifest` job
rewrites `k8s/ml-prediction-deployment.yaml` and pushes back to `main`.
By default the `GITHUB_TOKEN` is read-only — flip it once:

1. On GitHub: **Repo → Settings → Actions → General**.
2. Scroll to **Workflow permissions**.
3. Select **Read and write permissions** → **Save**.
4. Also tick **Allow GitHub Actions to create and approve pull requests**.

---

## Step 14 — Trigger the workflow, watch CI run

The first push already triggered it. Open your repo → **Actions** tab.
Three jobs:

1. `lint` — `ruff check`.
2. `build-and-push` — builds the image and pushes to
   `docker.io/YOUR-DOCKERHUB-USER/ml-prediction` with `:latest` and `:<sha>`.
3. `update-manifest` — rewrites the tag, commits back with `[skip ci]`.

After it finishes, refresh the repo's main branch — a new commit
appears: `chore: bump image to <sha> [skip ci]`.

**Make the Docker Hub repository public** so the cluster can pull it
without auth (the repo is created automatically on first push; new repos
default to private):

1. Docker Hub → **Repositories** → `YOUR-DOCKERHUB-USER/ml-prediction`.
2. **Settings → Visibility → Make public**.

(Alternatively, keep it private and add an `imagePullSecret` to the
Deployment — but public is simpler for a demo.)

Sanity-pull from your laptop:

```bash
docker pull docker.io/YOUR-DOCKERHUB-USER/ml-prediction:latest
```

---

## Step 15 — Install ArgoCD

```bash
kubectl create namespace argocd

kubectl apply -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

kubectl -n argocd wait --for=condition=available --timeout=180s \
  deployment/argocd-server
```

The `argocd-server` Service is `ClusterIP` by default. Convert it to a
fixed-port NodePort so you can hit it from the host without keeping a
`kubectl port-forward` running:

```bash
kubectl -n argocd patch svc argocd-server -p '{
  "spec": {
    "type": "NodePort",
    "ports": [
      {"port": 80,  "nodePort": 30084},
      {"port": 443, "nodePort": 30081}
    ]
  }
}'
```

Verify the NodePorts are live:

```bash
kubectl -n argocd get svc argocd-server
# argocd-server   NodePort   10.x.x.x   <none>   80:30084/TCP,443:30081/TCP   ...
```

Open <https://localhost:30081> (accept the self-signed cert).

Get the initial admin password:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d ; echo
```

Log in as `admin` with that password.

---

## Step 16 — Install Prometheus + Grafana

> **Install this before applying the ArgoCD Application (Step 17).** The
> `k8s/` folder ArgoCD syncs includes `ml-prediction-servicemonitor.yaml`,
> whose `kind: ServiceMonitor` is a *custom resource* provided by the
> kube-prometheus-stack chart below. If that CRD is missing when ArgoCD
> first syncs, ArgoCD rejects the **entire** sync —
> `one or more synchronization tasks are not valid` — and *nothing*
> deploys, not even the app and database. Installing the stack first
> registers the CRD so the sync validates cleanly.

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

kubectl create namespace monitoring

helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set grafana.adminPassword=admin \
  --set nodeExporter.enabled=false \
  --set prometheus-node-exporter.enabled=false \
  --set grafana.service.type=NodePort \
  --set grafana.service.nodePort=30082 \
  --set prometheus.service.type=NodePort \
  --set prometheus.service.nodePort=30083
```

Wait for Grafana (~2 min on first install):

```bash
kubectl -n monitoring wait --for=condition=ready pod \
  --selector=app.kubernetes.io/name=grafana --timeout=300s
```

Confirm the `ServiceMonitor` CRD is now registered — this is the one
ArgoCD needs in Step 17:

```bash
kubectl get crd servicemonitors.monitoring.coreos.com
# NAME                                    CREATED AT
# servicemonitors.monitoring.coreos.com   ...
```

Verify the NodePorts are live:

```bash
kubectl -n monitoring get svc kube-prometheus-stack-grafana kube-prometheus-stack-prometheus
# kube-prometheus-stack-grafana       NodePort   10.x.x.x   <none>   80:30082/TCP
# kube-prometheus-stack-prometheus    NodePort   10.x.x.x   <none>   9090:30083/TCP
```

Open in the browser — no `port-forward` needed:

- **Grafana** → <http://localhost:30082>  (login: `admin` / `admin`)
- **Prometheus** → <http://localhost:30083>

(Verifying Prometheus actually *scrapes* the app comes later in Step 20,
once the app pods are running and serving `/metrics`.)

> **If you already ran the `helm install` above without the NodePort
> flags**, flip the existing services without re-installing:
>
> ```bash
> helm upgrade kube-prometheus-stack prometheus-community/kube-prometheus-stack \
>   --namespace monitoring --reuse-values \
>   --set grafana.service.type=NodePort \
>   --set grafana.service.nodePort=30082 \
>   --set prometheus.service.type=NodePort \
>   --set prometheus.service.nodePort=30083
> ```

---

## Step 17 — Apply the ArgoCD Application

With the `ServiceMonitor` CRD registered in Step 16, ArgoCD can validate
every manifest in `k8s/` and sync the whole app.

```bash
kubectl apply -n argocd -f argocd/ml-prediction-app.yaml
```

An `ml-prediction` tile appears in the UI. It will be **OutOfSync** at
first. Click it → **SYNC → SYNCHRONIZE** (or wait — `automated +
selfHeal` will sync it within a few minutes).

Wait for everything to settle:

```bash
kubectl wait --for=condition=ready pod -l app=postgres      --timeout=180s
kubectl wait --for=condition=ready pod -l app=ml-prediction --timeout=180s
kubectl get pods
```

---

## Step 18 — (Optional) NGINX ingress for clean hostnames

NodePort already works (Step 19). If you want
`http://ml.prediction.vbo.local` instead, install the NGINX ingress
controller (one-time per cluster):

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.2/deploy/static/provider/cloud/deploy.yaml

kubectl -n ingress-nginx wait --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=180s
```

Map the hostname to localhost:

```bash
sudo sh -c 'echo "127.0.0.1   ml.prediction.vbo.local" >> /etc/hosts'
```

Verify the ingress now has an `ADDRESS`:

```bash
kubectl get ingress
# NAME                    CLASS   HOSTS                     ADDRESS     PORTS   AGE
# ml-prediction-ingress   nginx   ml.prediction.vbo.local   localhost   80      1m
```

---

## Step 19 — (Optional) Provide LLM API keys

Only needed if you want `/llm/chat` to actually answer. The other
endpoints (iris, advertising) work without this.

Configure everything in a `.env` file, then load it into a Secret:

```bash
cp .env.example .env        # then edit .env and fill in your keys
kubectl create secret generic app-secret --from-env-file=.env
kubectl rollout restart deployment ml-prediction-deployment
```

`.env` is gitignored, so your keys never get committed. The deployment
marks every key as `optional: true`, so the pod still boots without the
Secret — only `/llm/chat` errors (HTTP 503) until a key is present.

**Choosing a provider.** `LLM_PROVIDER` in `.env` selects the backend:

| `LLM_PROVIDER` | Key needed | Model env (optional) | Default model |
|---|---|---|---|
| `google_genai` (default) | `GOOGLE_API_KEY` | `GEMINI_MODEL` | `gemini-2.5-flash-lite` |
| `openrouter` | `OPENROUTER_API_KEY` | `OPENROUTER_MODEL` | `openai/gpt-4o-mini` |

OpenRouter uses its OpenAI-compatible API, so any tool-calling model it
hosts works. Get keys at
[aistudio.google.com](https://aistudio.google.com/app/apikey) (Gemini)
or [openrouter.ai/keys](https://openrouter.ai/keys) (OpenRouter).

---

## Step 20 — Hit the app, see the metrics

NodePort works without ingress:

```bash
# iris (always produces metrics)
curl -X POST http://localhost:30080/prediction/iris \
  -H 'Content-Type: application/json' \
  -d '{"SepalLengthCm":5.1,"SepalWidthCm":3.5,"PetalLengthCm":1.4,"PetalWidthCm":0.2}'

# metrics endpoint
curl -s http://localhost:30080/metrics | head -30

# probe target
curl http://localhost:30080/healthz
```

Now that the app is serving `/metrics`, verify Prometheus actually picked
up the ServiceMonitor installed back in Step 16:
**Prometheus UI → Status → Targets** should list
`serviceMonitor/default/ml-prediction/0` in green within ~30 seconds.

Generate sustained traffic so Grafana has something to plot:

```bash
while true; do
  curl -s -X POST http://localhost:30080/prediction/iris \
    -H 'Content-Type: application/json' \
    -d '{"SepalLengthCm":5.1,"SepalWidthCm":3.5,"PetalLengthCm":1.4,"PetalWidthCm":0.2}' > /dev/null
  sleep 0.2
done
```

In Grafana (left sidebar → **+** → **Dashboard → Add visualization**),
pick the `Prometheus` data source and paste any of these PromQL queries:

| Panel | PromQL |
|---|---|
| Request rate by handler (req/s) | `sum by (handler) (rate(http_requests_total{job="ml-prediction"}[1m]))` |
| Request latency p95 (s) | `histogram_quantile(0.95, sum by (le, handler) (rate(http_request_duration_seconds_bucket{job="ml-prediction"}[5m])))` |
| 5xx error rate (req/s) | `sum(rate(http_requests_total{job="ml-prediction",status=~"5.."}[1m]))` |
| In-flight requests | `sum(http_requests_inprogress{job="ml-prediction"})` |

---

## Step 21 — Watch the full GitOps loop

1. Edit anything (e.g. add `print("hello")` to `main.py`).
2. Commit and push to `main`.
3. **Actions** tab: `lint` → `build-and-push` → `update-manifest` go green.
4. A new commit appears titled
   `chore: bump image to <sha> [skip ci]`.
5. ArgoCD UI: hit **REFRESH** on the `ml-prediction` tile (or wait
   the default 3 min). The app goes **OutOfSync** → `selfHeal` syncs.
6. `kubectl get pods -l app=ml-prediction -w` shows the rolling update.
7. Grafana's request-rate panel dips briefly during the rollout, recovers.

---

## Tear down

```bash
# Stop ArgoCD watching the app (keeps the cluster otherwise)
kubectl -n argocd delete -f argocd/ml-prediction-app.yaml

# Drop the workload
kubectl delete -f k8s/

# Optional secret cleanup
kubectl delete secret app-secret 2>/dev/null

# Uninstall monitoring
helm uninstall kube-prometheus-stack -n monitoring
kubectl delete namespace monitoring

# Uninstall ArgoCD
kubectl delete -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl delete namespace argocd

# Uninstall NGINX ingress (if you installed it in Step 18)
kubectl delete -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.2/deploy/static/provider/cloud/deploy.yaml
```

Stop Kubernetes entirely:
Docker Desktop → **Settings → Kubernetes → uncheck "Enable Kubernetes"**.

---

## Common things that go wrong

### ArgoCD: `ComparisonError: repository not accessible`

Your fork is private. Easiest fix for a classroom demo: make the fork
public. Otherwise configure repo credentials at **ArgoCD → Settings →
Repositories → CONNECT REPO**.

### `update-manifest` job: `Permission denied to github-actions[bot]`

The workflow can't push back. Re-do Step 13:
**Repo → Settings → Actions → General → Workflow permissions** →
**Read and write permissions** → Save. Re-run the failed job.

### Pod stuck in `ImagePullBackOff` (`denied` / `not found`)

The Docker Hub repository is private (new repos default to private), or
CI hasn't pushed the image yet. Check the **Actions** tab shows a green
`build-and-push`, then make the repo public — the "Make the Docker Hub
repository public" part of Step 14. Verify it exists and is pullable:

```bash
docker pull docker.io/YOUR-DOCKERHUB-USER/ml-prediction:latest
```

### `ServiceMonitor` doesn't appear under Prometheus → Status → Targets

Prometheus only picks up `ServiceMonitor`s whose `release:` label
matches its selector. The default `kube-prometheus-stack` helm install
uses `release: kube-prometheus-stack` — which is what
`k8s/ml-prediction-servicemonitor.yaml` already sets. If you installed
the chart under a different release name, edit the label to match:

```bash
helm get values <release> -n monitoring | grep -A2 serviceMonitorSelector
```

### Pod `CrashLoopBackOff` right after first deploy

- Postgres isn't ready yet — confirm: `kubectl get pod -l app=postgres`.
  Once it's `Running`, the FastAPI pod usually recovers on its next
  restart.
- Or a typo in your latest commit. Look at:
  `kubectl logs -l app=ml-prediction --tail=50`.

### Ingress shows no `ADDRESS`

You skipped Step 18 (NGINX install). Either run Step 18 or stick with
NodePort (`http://localhost:30080`).

### CI runs in an infinite loop

The `[skip ci]` marker in the bump commit message is what prevents
this — keep it if you customise the workflow.

### ArgoCD: `failed to list refs ... lookup github.com ... server misbehaving`

In-cluster DNS can't resolve `github.com`, so ArgoCD's repo-server
can't fetch the manifests. Two reasons this hits you:

- You skipped the **Fix cluster DNS** block in Step 0 (or this is a
  fresh laptop). Run that block now — it patches the CoreDNS
  ConfigMap to forward to `8.8.8.8` / `1.1.1.1` instead of the WSL2
  host resolver, then restarts CoreDNS.
- You ran the patch, but then used **Docker Desktop → Settings →
  Kubernetes → Reset Kubernetes Cluster**. A reset wipes the CoreDNS
  ConfigMap back to defaults — re-apply the Step 0 block. (Or
  permanently fix it via **Settings → Resources → Network → Manual
  DNS configuration** → `8.8.8.8`, which survives resets.)

After fixing DNS, force ArgoCD to retry:

```bash
kubectl -n argocd annotate app ml-prediction \
  argocd.argoproj.io/refresh=hard --overwrite
```

### node-exporter `CrashLoopBackOff`: `path / ... not a shared or slave mount`

On **Docker Desktop / WSL2**, `kube-prometheus-stack`'s node-exporter
can't bind-mount the host root filesystem (the VM doesn't expose `/`
as a shared/slave mount), so the container fails with
`ContainerCannotRun`. Disable the host-root mount:

```bash
helm upgrade kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring --reuse-values \
  --set prometheus-node-exporter.hostRootFsMount.enabled=false
kubectl -n monitoring rollout status \
  ds/kube-prometheus-stack-prometheus-node-exporter
```

You lose host-filesystem metrics (disk usage of the node), but all the
app/cluster metrics this demo cares about still work.

### ArgoCD: `one or more synchronization tasks are not valid` — *after* installing the CRD

If the sync still fails with
`The Kubernetes API could not find monitoring.coreos.com/ServiceMonitor`
even though `kubectl get crd servicemonitors.monitoring.coreos.com`
succeeds, ArgoCD is holding a **stale discovery cache** — it listed the
cluster's API resources before the CRD existed. Refresh it:

```bash
kubectl -n argocd rollout restart \
  deploy/argocd-repo-server statefulset/argocd-application-controller
kubectl -n argocd annotate app ml-prediction \
  argocd.argoproj.io/refresh=hard --overwrite
```

A hard refresh alone updates the diff but can sit in retry-backoff; to
force a fresh sync immediately:

```bash
argocd app sync ml-prediction          # if you have the argocd CLI
# or, with kubectl only:
kubectl -n argocd patch app ml-prediction --type merge \
  -p '{"operation":{"initiatedBy":{"username":"admin"},"sync":{"revision":"main","prune":true,"syncStrategy":{"apply":{}}}}}'
```

### Pod `InvalidImageName` after first sync

The Deployment still has a placeholder image like
`docker.io/YOUR-DOCKERHUB-USER/ml-prediction:latest`. Replace
`YOUR-DOCKERHUB-USER` in `k8s/ml-prediction-deployment.yaml` with your
Docker Hub username (e.g.
`docker.io/erkansirin78/ml-prediction:latest`), then commit and push —
ArgoCD deploys from the repo, not your local working tree, so the edit
only takes effect once it's on `main`.
