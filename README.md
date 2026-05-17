# Orchestraty

Enterprise AI Operating System — a unified platform for data ingestion, structuring, exploratory analysis, rule-based labeling, and (coming soon) model training, deployment, and monitoring.

Built by [Thoughtrons](mailto:samrat@thoughtrons.in).

---

## Quick Start

### Prerequisites

- Docker Desktop (running)
- PowerShell 5.1+ (Windows) or Bash (Linux/macOS)
- Python 3.11+ with `streamlit` installed (`pip install streamlit`)

### One-Command Setup

```powershell
# 1. Copy the environment template
cp .env.example .env        # edit credentials if desired

# 2. Build and start everything
.\setup.ps1                  # builds Docker images, starts 6 services, creates admin user

# Fresh start (wipes all databases, storage, and cache):
.\setup.ps1 -Fresh
```

The setup script handles Docker Compose, waits for every service to be healthy, and auto-creates the initial superuser from `.env` credentials.

### Start the Frontends

```powershell
# Terminal 1 — Main pipeline dashboard
streamlit run frontend/app.py

# Terminal 2 — Admin control panel
streamlit run frontend/admin.py --server.port 8502
```

### Access Points

| Service               | URL                                   |
|-----------------------|---------------------------------------|
| Main App (Streamlit)  | http://localhost:8501                 |
| Admin Panel           | http://localhost:8502                 |
| API + Swagger Docs    | http://localhost:8000/docs            |
| MinIO Console         | http://localhost:9001                 |
| Qdrant Dashboard      | http://localhost:6333/dashboard       |

---

## Architecture

```
                         +-------------------+
                         |   Streamlit UIs   |
                         |  (app + admin)    |
                         +--------+----------+
                                  |
                         +--------v----------+
                         |   FastAPI (8000)  |
                         |  8 route modules  |
                         +---+-----+-----+--+
                             |     |     |
              +--------------+     |     +--------------+
              |                    |                     |
     +--------v-------+  +--------v--------+  +---------v--------+
     |  PostgreSQL     |  |  Redis          |  |  MinIO (S3)      |
     |  (users, tasks, |  |  (cache, Celery |  |  (file storage)  |
     |   datasets)     |  |   broker)       |  |                  |
     +--------+--------+  +--------+--------+  +------------------+
              |                    |
              |           +--------v--------+
              |           |  Celery Worker  |
              |           |  (structuring,  |
              |           |   EDA, labeling)|
              |           +--------+--------+
              |                    |
              |           +--------v--------+
              +-----------+  Qdrant         |
                          |  (vector store) |
                          +-----------------+
```

### Services (Docker Compose)

| Container              | Purpose                                  | Port  |
|------------------------|------------------------------------------|-------|
| orchestraty-api        | FastAPI backend                           | 8000  |
| orchestraty-postgres   | PostgreSQL 15 (persistent data)           | 5432  |
| orchestraty-redis      | Redis 7 (cache + Celery broker)           | 6379  |
| orchestraty-minio      | MinIO (S3-compatible file storage)        | 9000/9001 |
| orchestraty-qdrant     | Qdrant (vector database for embeddings)   | 6333  |
| orchestraty-celery-worker | Celery worker (background pipelines)   | -     |

---

## Features

### Phase 1 — Data Foundation (Complete)

**Authentication & RBAC**
- JWT-based auth with four roles: `admin`, `reviewer`, `annotator`, `viewer`
- Role-gated endpoints — each route enforces minimum role via `RoleChecker` middleware
- Self-service registration (defaults to `viewer` role); admin promotes via dashboard or CLI

**Data Ingestion & Versioning**
- Upload CSV, TSV, JSON, JSONL files with automatic validation
- Schema detection (column types, nulls, unique counts)
- Immutable version history per dataset with schema hash tracking
- File storage in MinIO with organized path structure

**Audit Logging**
- Every write action logged with user, action type, resource, timestamp
- Queryable by user, action, resource type, and date range

### Phase 2 — Intelligence Pipeline (Complete)

**Data Structuring Agent**
- Schema inference with type detection (numeric, categorical, datetime, text, boolean)
- Automated cleaning: null handling (5 strategies), duplicate removal, outlier detection (IQR)
- Quality scoring on 5 dimensions (completeness, consistency, validity, uniqueness, accuracy)
- Letter-grade output (A-F) with actionable recommendations

**AI-Powered EDA**
- Statistical profiling: per-column stats, correlations, skewness/kurtosis, data warnings
- Embedding generation: sentence-transformers (GPU) with TF-IDF + SVD fallback
- Clustering analysis: K-Means with PCA dimensionality reduction and silhouette scoring
- Optional Qdrant vector storage for semantic search

**Rule-Based Labeling Engine**
- 14 operators: `equals`, `not_equals`, `contains`, `not_contains`, `starts_with`, `ends_with`, `greater_than`, `less_than`, `greater_equal`, `less_equal`, `regex_match`, `in_list`, `is_null`, `is_not_null`
- 3 conflict resolution strategies: `first_match`, `highest_priority`, `all`
- Priority-based rule ordering
- Warnings for rules targeting non-existent columns
- Per-rule match statistics and label distribution reporting

**Background Task System**
- Celery-powered async pipelines with Redis broker
- Real-time progress tracking (percentage + status message)
- Race-condition-safe: task records committed before Celery dispatch
- Frontend auto-polls for results — no manual refresh needed

**Admin Dashboard**
- User management: list, promote/demote roles, activate/deactivate, delete
- Safety guards: last admin can't be demoted; admin can't delete/deactivate themselves
- System stats: user counts, dataset counts, task success/failure rates
- Service health monitoring: API, MinIO, Qdrant status checks
- Task monitor with filtering by type and status
- Audit trail viewer

### Phase 3 — ML Operations (Planned)

- Model training pipelines (AutoML + custom)
- Model registry and versioning
- One-click deployment with A/B testing
- Performance monitoring and drift detection

### Phase 4 — Enterprise Features (Planned)

- Active learning feedback loops
- Multi-tenant organization support
- Custom workflow builder
- API rate limiting and usage analytics

---

## Project Structure

```
ai-operating-system/
├── app.py                            # FastAPI entry point (8 routers, lifespan hooks)
├── setup.ps1                         # One-command setup (Docker + health checks + admin user)
├── manage.py                         # CLI admin commands (list-users, promote, reset-password)
├── .env.example                      # Environment template
├── requirements.txt                  # Python dependencies
│
├── core/                             # Shared platform core
│   ├── settings.py                   # Pydantic-settings config (reads .env)
│   ├── database.py                   # Async SQLAlchemy engine, session factory, init_db
│   ├── database_sync.py              # Sync session for Celery workers + CLI
│   ├── models/                       # ORM models
│   │   ├── user.py                   #   User (id, email, role, is_active)
│   │   ├── dataset.py                #   Dataset, DatasetVersion, DatasetMetadata
│   │   ├── background_task.py        #   BackgroundTask (celery tracking)
│   │   └── audit_log.py              #   AuditLog (action trail)
│   ├── api/                          # Route modules
│   │   ├── auth_routes.py            #   /api/v1/auth (register, login, me)
│   │   ├── admin_routes.py           #   /api/v1/admin (users, stats)
│   │   ├── task_routes.py            #   /api/v1/tasks (list, get, filter)
│   │   ├── audit_routes.py           #   /api/v1/audit (query logs)
│   │   ├── structuring_routes.py     #   /api/v1/structuring (run, results)
│   │   ├── eda_routes.py             #   /api/v1/eda (run, results, profile)
│   │   └── labeling_routes.py        #   /api/v1/labeling (run, results, operators)
│   ├── middleware/
│   │   └── rbac.py                   #   RoleChecker + require_admin/reviewer/annotator/viewer
│   └── services/
│       ├── auth_service.py           #   JWT create/verify, password hash, get_current_user
│       ├── audit_service.py          #   log_action()
│       └── task_service.py           #   create_task_record, update_task_progress, complete_task
│
├── data-intelligence-system/         # Processing modules (hyphenated dir, imported via sys.path)
│   ├── ingestion/                    # File upload, validation, MinIO storage
│   │   ├── api/                      #   routes.py, schemas.py
│   │   ├── storage/                  #   minio_client.py
│   │   └── services/                 #   validation, versioning
│   ├── structuring/                  # Schema detection + cleaning + quality
│   │   ├── schema_detector.py        #   Type inference, null/unique analysis
│   │   ├── cleaning_pipeline.py      #   Null fill, dedup, outlier removal
│   │   └── quality_scorer.py         #   5-dimension quality grading (A-F)
│   ├── eda/                          # Exploratory data analysis
│   │   ├── profiler.py               #   Per-column stats, correlations, warnings
│   │   ├── embedder.py               #   Sentence-transformers / TF-IDF+SVD
│   │   └── clusterer.py              #   K-Means + PCA, silhouette scoring
│   └── labeling/                     # Rule-based labeling
│       ├── rule_engine.py            #   14 operators, 3 conflict strategies
│       └── rule_store.py             #   Sync + async rule persistence
│
├── data_intelligence/                # Celery task definitions
│   ├── celery_app.py                 #   Celery config (Redis broker)
│   └── tasks/
│       ├── structuring_tasks.py
│       ├── eda_tasks.py
│       └── labeling_tasks.py
│
├── frontend/                         # Streamlit apps
│   ├── app.py                        #   Main pipeline UI (upload > structure > EDA > label)
│   └── admin.py                      #   Admin panel (users, tasks, health, audit)
│
├── infrastructure/
│   ├── docker-compose.yml            #   6-service stack
│   ├── Dockerfile                    #   Python 3.11 API image
│   ├── k8s/                          #   Kubernetes manifests (planned)
│   └── terraform/                    #   IaC templates (planned)
│
└── tests/
    ├── integration/
    └── e2e/
```

---

## API Endpoints

### Authentication (`/api/v1/auth`)

| Method | Endpoint       | Role    | Description                |
|--------|---------------|---------|----------------------------|
| POST   | /register      | public  | Create account (viewer role)|
| POST   | /login         | public  | Get JWT access token        |
| GET    | /me            | any     | Current user profile        |

### Datasets (`/api/v1/datasets`)

| Method | Endpoint                    | Role      | Description                  |
|--------|-----------------------------|-----------|------------------------------|
| POST   | /                           | annotator | Create dataset               |
| GET    | /                           | viewer    | List datasets                |
| GET    | /{id}                       | viewer    | Get dataset + versions       |
| POST   | /{id}/upload                | annotator | Upload file version          |
| PUT    | /{id}/status                | reviewer  | Update dataset status        |
| PUT    | /{id}/metadata              | annotator | Upsert metadata key-value    |
| DELETE | /{id}                       | admin     | Delete dataset + cascade     |

### Structuring (`/api/v1/structuring`)

| Method | Endpoint         | Role      | Description                           |
|--------|-----------------|-----------|---------------------------------------|
| POST   | /run             | annotator | Start structuring pipeline            |
| GET    | /results/{id}    | viewer    | Get structuring results for dataset   |

### EDA (`/api/v1/eda`)

| Method | Endpoint         | Role      | Description                    |
|--------|-----------------|-----------|--------------------------------|
| POST   | /run             | annotator | Start EDA pipeline             |
| GET    | /results/{id}    | viewer    | Get EDA results for dataset    |
| GET    | /profile/{id}    | viewer    | Get column profiles only       |

### Labeling (`/api/v1/labeling`)

| Method | Endpoint            | Role      | Description                      |
|--------|---------------------|-----------|----------------------------------|
| POST   | /run                | annotator | Start labeling pipeline          |
| GET    | /results/{id}       | viewer    | Get labeling results for dataset |
| GET    | /rules/operators    | viewer    | List all supported operators     |

### Admin (`/api/v1/admin`)

| Method | Endpoint              | Role  | Description                    |
|--------|-----------------------|-------|--------------------------------|
| GET    | /users                | admin | List all users (filterable)    |
| PUT    | /users/{id}/role      | admin | Change user role               |
| PUT    | /users/{id}/status    | admin | Activate/deactivate user       |
| DELETE | /users/{id}           | admin | Permanently delete user        |
| GET    | /stats                | admin | System-wide statistics         |

### Tasks & Audit

| Method | Endpoint            | Role   | Description                        |
|--------|---------------------|--------|------------------------------------|
| GET    | /api/v1/tasks/      | viewer | List tasks (filter by type/status) |
| GET    | /api/v1/tasks/{id}  | viewer | Get single task                    |
| GET    | /api/v1/audit/      | admin  | Query audit logs                   |

---

## Configuration

All configuration is via environment variables (`.env` file). See `.env.example` for the full list.

Key settings:

| Variable                    | Default                          | Description                        |
|-----------------------------|----------------------------------|------------------------------------|
| DATABASE_URL                | postgresql+asyncpg://...         | PostgreSQL connection string       |
| MINIO_ENDPOINT              | minio:9000                       | MinIO server address               |
| REDIS_URL                   | redis://redis:6379/0             | Redis connection                   |
| SECRET_KEY                  | (change in production)           | JWT signing key                    |
| ACCESS_TOKEN_EXPIRE_MINUTES | 60                               | Token lifetime                     |
| QDRANT_HOST                 | qdrant                           | Vector DB host                     |
| EMBEDDING_MODEL             | all-MiniLM-L6-v2                 | Sentence-transformers model        |
| ADMIN_EMAIL                 | admin@orchestraty.local          | Initial superuser email            |
| ADMIN_PASSWORD              | Admin@12345                      | Initial superuser password         |

---

## CLI Management

Run commands inside the API container:

```powershell
docker exec -it orchestraty-api python manage.py list-users
docker exec -it orchestraty-api python manage.py promote-admin user@example.com
docker exec -it orchestraty-api python manage.py set-role user@example.com reviewer
docker exec -it orchestraty-api python manage.py deactivate-user user@example.com
docker exec -it orchestraty-api python manage.py reset-password user@example.com
docker exec -it orchestraty-api python manage.py create-superuser   # interactive
```

---

## Useful Commands

```powershell
# View API logs
docker compose -f infrastructure/docker-compose.yml logs -f api

# View Celery worker logs
docker compose -f infrastructure/docker-compose.yml logs -f celery-worker

# Stop all services
docker compose -f infrastructure/docker-compose.yml down

# Full reset (wipes all data)
.\setup.ps1 -Fresh

# Rebuild just the API after code changes
docker compose -f infrastructure/docker-compose.yml up -d --build api celery-worker
```

---

## License

Proprietary — Thoughtrons. All rights reserved.
