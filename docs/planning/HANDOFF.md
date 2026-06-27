# Orchestraty — Project Handoff

> Date of handoff: 2026-06-15. Scope: every change since `git HEAD = fafafb7 "Phase 3 refined and redesigned"`, covering Phases A through F of the single-user pivot, plus the marketing-site rebuild and the `/tour` walkthrough.

This document is written for whoever (or whichever Claude session) picks up the work next. It is exhaustive on purpose — read the sections you need, ignore the rest.

---

## 1. Project identity (what Orchestraty is, *now*)

Orchestraty is an open-source, **single-user, local-first** data operating system. It runs on the user's own machine via Docker Compose, exposes a FastAPI backend and a React dashboard, and orchestrates ingest → structure → EDA → label → review → workflow → export over the user's datasets. It's MIT-licensed, free forever, and ships zero accounts / no telemetry.

There are also two **sibling testbeds** in the same workspace folder that are intentionally **out of scope** for the current pivot:
- `knowit-app/` — codebase intelligence testbed (Know Your Code)
- `secure-code-app/` — security scanning testbed (Aegis)

These have their own setup scripts and own backends; the work in Phases A–F never touched them.

The flagship project lives at `C:\Users\prave\Orchestraty\ai-operating-system\` and is the only thing Phases A–F edited.

---

## 2. The strategic pivot (in the user's words)

Decisions made by the user before Phase A, paraphrased from chat:

- **Distribution**: `pip install orchestraty` + Docker Compose, localhost-only. Inspired by MLflow.
- **No pricing.** Open source, donation page only. Provider TBD.
- **No team mode, no admin, no SuperAdmin.** Single user only.
- **Frontend split**: marketing website ≠ localhost app. Two Vite entries.
- **Add a `-Website` flag** to `setup.ps1` for working on the marketing site.
- **A tour/simulation walkthrough** in the future.

These decisions drove every phase that followed.

---

## 3. Repository layout (only the relevant subtrees)

```
C:\Users\prave\Orchestraty\                              # workspace folder
├── HANDOFF.md                                            # ← this file
├── ai-operating-system\                                  # THE flagship project
│   ├── app.py                                           # FastAPI entrypoint + lifespan
│   ├── alembic.ini
│   ├── alembic\                                          # migrations
│   │   ├── env.py                                       # Phase D-patched (no more User/Audit/Permission imports)
│   │   └── versions\
│   │       ├── b30963890f73_add_background_tasks_table.py
│   │       ├── c41a8b2e1f90_add_llm_configs_table.py
│   │       ├── d52b7c3f2a81_add_image_assets_table.py
│   │       ├── e63d8a4f5b92_add_workflow_tables.py
│   │       └── f74e9b5c6d83_drop_users_orgs_audit_logs.py  # ← Phase D migration
│   ├── core\
│   │   ├── api\                                          # 9 route files now (was 12)
│   │   │   ├── ai_labeling_routes.py
│   │   │   ├── eda_routes.py
│   │   │   ├── image_routes.py
│   │   │   ├── labeling_routes.py
│   │   │   ├── llm_routes.py
│   │   │   ├── review_routes.py
│   │   │   ├── structuring_routes.py
│   │   │   ├── task_routes.py
│   │   │   └── workflow_routes.py
│   │   ├── models\                                       # 5 model files now (was 8)
│   │   │   ├── __init__.py                              # exports only the 5
│   │   │   ├── background_task.py
│   │   │   ├── dataset.py
│   │   │   ├── image_asset.py
│   │   │   ├── llm_config.py
│   │   │   └── workflow.py
│   │   ├── services\                                     # 3 files (was 5)
│   │   │   ├── auth_service.py                          # 59-line no-DB synthetic User stub
│   │   │   ├── dataset_access_service.py                # no-op shims
│   │   │   └── task_service.py
│   │   ├── middleware\                                   # empty after rbac.py delete
│   │   ├── llm\, paths.py, settings.py, storage.py, database*.py
│   ├── data-intelligence-system\                         # ingestion package (Phase D8 hotfix area)
│   │   └── ingestion\
│   │       ├── api\routes.py                            # Phase D-patched
│   │       ├── api\schemas.py                           # Phase D-patched
│   │       └── services\dataset_service.py              # Phase D-patched
│   ├── data-intelligence-system\labeling\rule_store.py   # Phase D9 hotfix
│   ├── data_intelligence\tasks\*.py                      # Celery tasks (Phase D10 — already clean)
│   ├── frontend\Frontend\                                # the React frontend
│   │   ├── index.html                                   # app entry HTML
│   │   ├── website.html                                 # website entry HTML
│   │   ├── vite.config.ts                               # app config (port 5173)
│   │   ├── vite.website.config.ts                       # website config (port 5273)
│   │   ├── package.json                                 # 6 scripts: dev/dev:website + build/build:website + lint/preview
│   │   ├── tsconfig*.json
│   │   └── src\
│   │       ├── app\                                     # ← localhost dashboard, served at :5173
│   │       │   ├── main.tsx
│   │       │   ├── AppRouter.tsx                        # mounts DashboardLayout at /
│   │       │   ├── context\AuthContext.tsx              # synthetic LOCAL_USER context
│   │       │   ├── layouts\DashboardLayout.tsx          # sidebar + outlet
│   │       │   ├── components\TaskMonitor\
│   │       │   ├── hooks\usePolling.ts
│   │       │   └── pages\                               # 13 dashboard pages
│   │       │       ├── dashboard\, datasets\, structuring\, eda\,
│   │       │       ├── labeling\, ai-labeling\, images\, review\,
│   │       │       ├── workflows\, llm\, tasks\
│   │       ├── website\                                 # ← marketing site, served at :5273
│   │       │   ├── website-main.tsx
│   │       │   ├── WebsiteApp.tsx                       # landing composition
│   │       │   ├── context\AuthContext.tsx              # anonymous stub
│   │       │   ├── pages\
│   │       │   │   ├── pricing\PricingPage.tsx          # "Free forever" open-source page
│   │       │   │   └── tour\                            # /tour walkthrough
│   │       │   │       ├── Tour.tsx                     # 8 architectural stages + mind map
│   │       │   │       └── Tour.css
│   │       │   └── components\
│   │       │       ├── Navbar\                          # Docs + Install CTAs
│   │       │       ├── Hero\                            # centered, install pill + tour CTA
│   │       │       ├── Stack\                           # "Built on…" strip (NEW)
│   │       │       ├── Features\                        # 6 tiles
│   │       │       ├── HowItWorks\
│   │       │       ├── DeepDive\                        # 3 architectural walkthroughs (NEW)
│   │       │       ├── UseCases\
│   │       │       ├── Integrations\
│   │       │       ├── FAQ\                             # 8-item accordion (NEW)
│   │       │       ├── Updates\                         # Phase 1-5 timeline (NEW)
│   │       │       ├── FinalCTA\
│   │       │       ├── Footer\
│   │       │       ├── ParticleCanvas\                  # background dots+web
│   │       │       ├── Pricing\                         # standalone /pricing component
│   │       │       └── InstallPill\                     # reusable install command pill (NEW)
│   │       ├── shared\
│   │       │   ├── index.css                            # design tokens (Montserrat etc.)
│   │       │   ├── api\                                 # 11 axios api modules
│   │       │   ├── Logo\AntigravityLogo.tsx
│   │       │   └── stencil-components\
│   │       └── assets\
│   ├── infrastructure\docker-compose.yml
│   ├── requirements.txt                                  # no passlib/jose/bcrypt
│   ├── setup.ps1                                         # -Fresh + -Website flags
│   └── stop.ps1                                          # cleans ports 5173 + 5273
├── knowit-app\                                           # OUT OF SCOPE
├── secure-code-app\                                      # OUT OF SCOPE
└── codemap\                                              # static analysis tool (used elsewhere)
```

---

## 4. Phase A — De-authentication

### Goal
Remove every auth check from the running system so the user can hit any endpoint without a token. Keep file-shape compatibility so routes don't all need editing.

### What changed
| File | Change |
|---|---|
| `core/services/auth_service.py` | `get_current_user` rewritten to lazily create one fixed `LOCAL_USER` row (UUID `00000000-0000-0000-0000-000000000001`, role `superadmin`, email `local@orchestraty.local`). Kept `hash_password`, `verify_password`, `create_access_token`, `decode_access_token`, `oauth2_scheme` as importable shims so legacy `auth_routes.py` still loads. **Note: Phase D later replaced this with a no-DB dataclass.** |
| `core/services/dataset_access_service.py` | `is_superadmin` → True. `can_access_dataset` → True. `dataset_access_filter` → None. `assert_dataset_access` only checks the row exists (404 if not). Same function signatures so callers didn't need editing. |
| `frontend/Frontend/src/api/client.ts` (Phase B moved to `src/shared/api/client.ts`) | Dropped the request interceptor (no more `Authorization: Bearer`) and the response interceptor (no more redirect-to-login on 401). |
| `frontend/Frontend/src/context/AuthContext.tsx` (later moved to `src/app/context/`) | AuthProvider now exposes a constant synthetic `LOCAL_USER` and a non-null sentinel token. `login`/`register`/`logout` are no-ops. |
| `frontend/Frontend/src/AppRouter.tsx` | Dropped `Login` / `Register` imports. Replaced `/app/login` and `/app/register` with `<Navigate to="/app">` redirects. |

### Why role=superadmin still appears
`core/middleware/rbac.py` had `RoleChecker` deps (`require_admin`, `require_superadmin`, etc.) used throughout routes. By making `LOCAL_USER.role = "superadmin"` every existing role check passes by definition, so no individual route had to be edited in Phase A. Phase C/D dismantle the role machinery itself.

---

## 5. Phase B — Frontend split (app vs marketing)

### Goal
Two independent Vite entries from the same `frontend/Frontend/` directory:
- `index.html` + `src/app/main.tsx` → dashboard at `localhost:5173`
- `website.html` + `src/website/website-main.tsx` → marketing at `localhost:5273`

### What moved where
The migration was executed by a single Python script in chat (atomic file moves + AST-aware import rewriting):

**shared/** (used by both entries):
- `src/index.css` → `src/shared/index.css`
- `src/stencil-components/` → `src/shared/stencil-components/`
- `src/components/Logo/` → `src/shared/Logo/`
- `src/api/` (all 14 axios modules) → `src/shared/api/` — Phase C later deleted `admin.ts`, `audit.ts`, `auth.ts`

**app/** (dashboard-only):
- `src/AppRouter.tsx` → `src/app/AppRouter.tsx`
- `src/context/AuthContext.tsx` → `src/app/context/AuthContext.tsx`
- `src/layouts/` → `src/app/layouts/`
- `src/hooks/` → `src/app/hooks/`
- `src/components/TaskMonitor/` → `src/app/components/TaskMonitor/`
- `src/pages/{dashboard,datasets,structuring,eda,labeling,ai-labeling,images,review,workflows,llm,admin,tasks,audit,auth}/` → `src/app/pages/*`

**website/** (marketing-only):
- `src/App.tsx` → `src/website/WebsiteApp.tsx`
- `src/App.css` → `src/website/WebsiteApp.css`
- `src/components/{Navbar,Hero,Features,HowItWorks,UseCases,Integrations,FinalCTA,Footer,ParticleCanvas,UserHome,Pricing}/` → `src/website/components/*`
- `src/pages/pricing/` → `src/website/pages/pricing/`
- NEW: `src/website/context/AuthContext.tsx` — anonymous stub (returns `user: null` always) so marketing components behave as logged-out. Distinct from the app's AuthContext.

### Build config
- `vite.config.ts` — unchanged from default; binds port 5173, serves `index.html`.
- NEW: `vite.website.config.ts` — `port: 5273, strictPort: true`, serves `website.html` via a `name: 'website-root'` middleware plugin that rewrites `/` → `/website.html` (mirrors the SuperAdmin pattern that was later deleted). `outDir: 'dist-website'`. `rollupOptions.input: 'website.html'`.
- `package.json` scripts: `dev`, `dev:website`, `build`, `build:website`, `lint`, `preview`.

### setup.ps1 `-Website` flag
- Default: backend (`:8000`) + dashboard app (`:5173`).
- `-Website`: backend (`:8000`) + marketing site (`:5273`), the SuperAdmin launch is skipped (later removed entirely in Phase C).

### Verification of bundle isolation
A custom AST script proves the **import graphs are physically disjoint**:
- App entry reaches 44 files (30 app + 14 shared, 0 website, 0 superadmin).
- Website entry reaches 38 files (32 website + 6 shared, 0 app, 0 superadmin).
- Zero cross-imports between `src/app/` and `src/website/` anywhere.

---

## 6. Phase C — UI surface removal

### Goal
Delete the multi-tenant UI: SuperAdmin entry, Admin pages, Audit Log pages, role badges, role-gated routes, dead login/register pages.

### What got deleted
| Path | Type |
|---|---|
| `frontend/Frontend/src/superadmin/` (full directory, 9 files) | Vite entry + pages |
| `frontend/Frontend/superadmin.html` | Vite entry HTML |
| `frontend/Frontend/vite.superadmin.config.ts` | Vite config |
| `frontend/Frontend/src/app/pages/admin/` (Admin.tsx + .css) | Page |
| `frontend/Frontend/src/app/pages/audit/` (AuditLogs.tsx + .css) | Page |
| `frontend/Frontend/src/app/pages/auth/` (Login.tsx, Register.tsx, Auth.css) | Pages |
| `frontend/Frontend/src/shared/api/admin.ts` | API client |
| `frontend/Frontend/src/shared/api/audit.ts` | API client |
| `frontend/Frontend/src/shared/api/auth.ts` | API client |

### What got rewritten
| File | Change |
|---|---|
| `src/app/AppRouter.tsx` | Dropped `Admin`, `AuditLogs` imports + routes. Dropped all `/app/*` legacy redirects. Now mounts `DashboardLayout` at `/` with 13 nested routes (dashboard, datasets, datasets/:id, structuring, eda, labeling, ai-labeling, images, review, workflows, workflows/:id, llm, tasks) + a `*` fallback to `/`. |
| `src/app/layouts/DashboardLayout.tsx` | Removed `minRole`, `hasMinRole`, `ROLE_LEVEL` constants. Sidebar trimmed to 11 items (no Admin, no Audit Logs). Every `to="/app/X"` → `to="/X"`. Removed the `<span dash__user-role>` role badge. Removed the logout button (no auth to log out of). |
| `src/app/pages/dashboard/Dashboard.tsx` | Dropped `adminApi` import + role-gated stats fetch + the 4-stat-card grid. Now: welcome header, Quick Actions, Recent Tasks. |
| 8 page files (Datasets, EDA, Labeling, Structuring, Workflows, DatasetDetail, ImagePipeline, WorkflowDetail) | All `to="/app/X"` template literals + string links rewritten to `/X`. |
| `package.json` | Removed `dev:superadmin` + `build:superadmin` scripts. |
| `setup.ps1` | Renumbered to 6 steps (was 8). Removed the SuperAdmin launch block. Removed `.env` SuperAdmin email read. Port cleanup is now `@(5173, 5273)`. |
| `stop.ps1` | Port lists `@(5173, 5273)` and `@(5173, 5273, 8000, 5432, 6379)`. Wipe message no longer references SuperAdmin. |

### Bundle state after Phase C
- App: 30 files
- Website: 27 files (later grew with the Phase F additions)
- Shared: 15 files
- No SuperAdmin tree anywhere.

---

## 7. Phase D — Schema cleanup

### Goal
Drop the multi-tenant tables and FK columns from the database; delete the model/service/route files that backed them; rewrite `get_current_user` to a no-DB synthetic user; drop `passlib`/`python-jose`/`bcrypt` from `requirements.txt`.

### The migration
File: `alembic/versions/f74e9b5c6d83_drop_users_orgs_audit_logs.py`
- `revision = "f74e9b5c6d83"`, `down_revision = "e63d8a4f5b92"`
- `upgrade()` — idempotent (uses `sa.inspect()` to check before each drop):
  1. Drops FK-bearing columns from dependent tables first: `datasets.created_by`, `datasets.org_id`, `dataset_versions.created_by`, `background_tasks.created_by`, `workflows.created_by`, `llm_configs.user_id`.
  2. Drops the old `llm_configs` unique constraint `uq_user_provider` and re-creates `uq_provider` since the table is workspace-global now.
  3. Drops `audit_logs`, `dataset_permissions`, `users` tables.
  4. On PostgreSQL only: `DROP TYPE IF EXISTS user_role`.
- `downgrade()` — raises `NotImplementedError`. One-way pivot; the data can't be reconstructed.

### Files deleted
| Path | Type |
|---|---|
| `core/models/user.py` | Model |
| `core/models/audit.py` | Model (AuditLog) |
| `core/models/permission.py` | Model (DatasetPermission) |
| `core/middleware/rbac.py` | RBAC machinery |
| `core/services/audit_service.py` | log_action |
| `core/api/auth_routes.py` | /auth endpoints |
| `core/api/admin_routes.py` | /admin endpoints |
| `core/api/audit_routes.py` | /audit-logs endpoints |
| `manage.py` (top-level CLI helper) | All SuperAdmin promote/list/reset commands |

### Files rewritten
- `core/services/auth_service.py` — collapsed to 59 lines. Now defines `LOCAL_USER_ID`, `LOCAL_USER_EMAIL`, a frozen `User` dataclass (`id, email, full_name, role, is_active`), a constant `_LOCAL_USER` with `role="superadmin"`, and `async def get_current_user() -> User: return _LOCAL_USER`. **No DB hit, no parameters, no token validation.**
- `core/services/dataset_access_service.py` — collapsed to 58 lines of no-op shims (same as Phase A but now imports `User` from the dataclass module, not the deleted ORM model).
- `core/models/__init__.py` — exports only `Base, Dataset, DatasetVersion, DatasetMetadata, BackgroundTask, LLMConfigRecord, ImageAsset, Workflow, WorkflowTemplate`.
- `core/models/dataset.py` — dropped `created_by`, `org_id` columns from `Dataset`; dropped `created_by` from `DatasetVersion`; removed `creator` relationships.
- `core/models/background_task.py` — dropped `created_by`.
- `core/models/workflow.py` — dropped `created_by`.
- `core/models/llm_config.py` — dropped `user_id` column + FK. UniqueConstraint changed to `(provider)`. `__repr__` updated.
- `app.py` — dropped 3 router imports/includes (`auth_router`, `admin_router`, `audit_router`). Dropped the entire SuperAdmin auto-create block (was lines 159-217). No `core.models.user` import. No `hash_password` reference.
- `requirements.txt` — `passlib`, `python-jose`, `bcrypt` removed.
- `alembic/env.py` — dropped `User`, `AuditLog`, `DatasetPermission` from the model-import block. **This was critical; if missed, `alembic upgrade head` crashes at module load and the migration never runs.**
- `core/settings.py` — dropped the `# ── Auth / JWT ────` block (SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES). Note: `MINIO_SECRET_KEY` is unrelated and stays.
- `core/api/image_routes.py` — `_user_from_header_or_query` now just delegates to `get_current_user()` (was decoding a JWT from `?token=` query param). The `request` and `token` parameters are kept on the signature so existing call sites and URL shapes don't break.

### Routes patched (bulk regex pass)
Every file in `core/api/` and `data-intelligence-system/ingestion/api/routes.py` had:
- `from core.middleware.rbac import …` → dropped
- `from core.models.user import User` → `from core.services.auth_service import User, get_current_user`
- `from core.services.audit_service import log_action` → dropped
- `Depends(require_admin/superadmin/reviewer/annotator/viewer)` → `Depends(get_current_user)`
- `created_by=current_user.id,` / `created_by=str(current_user.id),` / `user_id=current_user.id,` → dropped (column gone)
- `await log_action(...)` multi-line blocks → dropped
- `LLMConfigRecord.user_id == current_user.id` filters → dropped + collapsed empty `.where()` calls
- `db, current_user.id, payload.provider, payload.model_name` (LLM service signature) → `db, payload.provider, payload.model_name`

### Hotfixes that landed during Phase D verification (D8, D9, D10)

After the first end-to-end test the user got a 502 then 500 on upload. The audits found:

**D8 — Ingestion package (lives outside `core/`)**:
- `data-intelligence-system/ingestion/api/routes.py` — same auth/permission/audit patches as `core/api/*.py`. **Plus**: deleted the entire `POST /{dataset_id}/permissions` endpoint (1838 bytes) since `DatasetPermission` is gone.
- `data-intelligence-system/ingestion/api/schemas.py` — deleted `PermissionCreate` and `PermissionResponse` classes. Plus the killer bug: deleted `created_by: UUID` and `org_id: Optional[str]` from `DatasetResponse`, and `created_by: UUID` from `DatasetVersionResponse`. **These required Pydantic fields on `model_validate(orm)` were the cause of the 500** — the ORM instances no longer had those attributes so Pydantic raised ValidationError.
- `data-intelligence-system/ingestion/services/dataset_service.py` — same import cleanup, dropped `DatasetPermission` row creation in `create_dataset`, simplified `list_datasets` to skip the permission-based filter.

**D9 — Labeling rule_store**:
- `data-intelligence-system/labeling/rule_store.py` — two `BackgroundTask(..., created_by=uuid.UUID(created_by) if created_by else None, …)` writes. Rule sets are stored as `BackgroundTask` rows with `task_type="ruleset"`. Both `created_by=` lines stripped.

**D10 — Celery task files**:
- `data_intelligence/tasks/*.py` (7 files) — already clean. Celery tasks operate on `dataset_id`/`version_id` passed via task signature, never needed auth context. Confirmed nothing required patching.

**`core/services/task_service.py`** — `create_task_record` had `created_by: UUID` required arg writing to `BackgroundTask.created_by`. Dropped the parameter, dropped the kwarg in the constructor, then swept the 10 caller sites in `core/api/*.py` to drop `created_by=current_user.id,` lines.

### Verification probes that came back clean
- `python -m compileall -q core/ app.py alembic/ data-intelligence-system/` exits 0
- 98+ cross-file `from core.X import Y` imports all resolve (AST-verified)
- 27 stale-pattern regex sweep (`passlib`, `jose`, `bcrypt`, `hash_password`, `verify_password`, `create_access_token`, `decode_access_token`, `oauth2_scheme`, `OAuth2PasswordBearer`, `JWT`, `require_admin/superadmin/reviewer/annotator/viewer`, `RoleChecker`, `minRole`, `AuditLog`, `log_action`, `audit_service`, `DatasetPermission`, `core.models.user`, `core.models.audit`, `core.models.permission`, `core.middleware.rbac`, `created_by=current_user`, `user_id=current_user`, `current_user.org_id`, `ACCESS_TOKEN_EXPIRE`, `ALGORITHM = `) → **0 hits anywhere**
- Live test of `auth_service.get_current_user()` returns a `User` dataclass with the fixed UUID
- Deep AST audit (current_user.X reads vs User dataclass fields; Pydantic from_attributes schemas vs ORM columns; constructor kwargs vs columns; refs to dropped columns) → 0 hard errors

### What is **not** yet verified
- Live `alembic upgrade head` against a populated Postgres — the sandbox can't reach Docker or PyPI. The migration is idempotent and reflection-guarded, so it's safe; first `.\setup.ps1` run on the user's machine should run it cleanly. The user has been instructed to use `.\setup.ps1 -Fresh` for a clean test.

---

## 8. Phase F — Marketing website rebuild

### Goal
Align the marketing site with the new theme (single-user, local-first, pip-installable, no accounts, open source), make the install command unmissable, add depth so the page properly reveals the project as you scroll, and build a cinematic `/tour` page.

### NEW component: `InstallPill`
Path: `src/website/components/InstallPill/InstallPill.tsx` + `.css`

A reusable dark-glass capsule with:
- Left: button-style "Install Orchestraty ▾" affordance (decorative chevron — Phase G might add real platform dropdown).
- Right: monospace `pip install orchestraty` command + copy icon that flashes a green check on click.
- Optional "Or see how it works ↓" footnote link (smooth-scrolls to `#how-it-works`).
- Two sizes: `lg` (Hero), `md` (FinalCTA + Pricing). Stacks vertically below 540px.
- `INSTALL_COMMAND = 'pip install orchestraty'` is the single source of truth.

### Auth CTA sweep (across the marketing site)
All Sign In / Sign Up / Create Account / Get Started / Start Free / Start with Pro / Contact Sales CTAs were replaced. Every `Link to="/app/login"` and `Link to="/app/register"` removed. Final whole-website grep returns 0 hits for those paths.

| Where | Before | After |
|---|---|---|
| **Navbar** (right side) | "Sign in" + "Get Started" buttons | "Docs" + "Install" — both anchor `#get-started`. Mobile menu matches. `useAuth` hook removed (was returning null anyway). |
| **Hero** | "Get started" Link + "See how it works" | `<InstallPill withFootnote />` as the centerpiece (was previously inline-left), plus a chunky purple-glow "▶ Take the tour — 2-minute walkthrough · no install required →" CTA below it linking to `/tour`. |
| **FinalCTA** | "Create your account" + "Sign in" | `<InstallPill size="md" />` only. Copy: "One install away from production-ready data." / "Runs entirely on your machine. Bring your own LLM keys. No accounts, no lock-in." |
| **Pricing** (`/pricing`) | Three tiers ($0 Community / $49 Pro / Enterprise Custom), monthly/annual toggle, SSO & SAML, `mailto:business@…` | Full rewrite. Single card: "Orchestraty — $0 forever — Open source · MIT licensed", install pill inside the card, 8-item feature list. Below: "Support development" panel with placeholder donation buttons ("Donation channels coming soon" / "Or just install and try it") and a "we're picking a provider" footnote. |
| **Features** (last tile) | tag "Enterprise", title "RBAC & Audit Logs", desc about admin/reviewer/annotator/viewer roles + JWT auth + user management dashboard | tag "Local-first", title "Your Data, Your Machine", desc: "Everything runs on your computer. No cloud, no accounts, no telemetry. Your datasets and API keys never leave your machine." |
| **Footer** | `/app/structuring`, `/app/ai-labeling`, `/app/images`, `/app/workflows`, `/app/llm`, `/app/login`, `/app/register` | All repointed to `/#features` or `/#get-started` so nothing 404s. |
| **Features cards / UseCases cards** | `link: '/app/X'` everywhere | All swept to `/#features`. |
| **WebsiteApp.tsx** | `{user ? <UserHome /> : <marketing branch>}` fork | Single composition. `useAuth` import gone. |
| **UserHome component** | Existed as dead code (user always null on website) with "Signed in as" / "your Orchestraty workspace" copy | **Deleted entire directory.** |
| **Navbar "Pricing" link** | "Pricing" | "Open source" (route stays `/pricing`) |

### NEW sections (made the page longer + revealing)

The user's complaint was the page was too short and repetitive. Five new sections added; `WebsiteApp.tsx` was rewired to the new scroll order:

```
Navbar
Hero               ← rewritten, centered, big install pill, tour CTA
Stack              ← NEW
Features
HowItWorks
DeepDive           ← NEW
UseCases
Integrations
FAQ                ← NEW
Updates            ← NEW
FinalCTA
Footer
```

| New section | What it is |
|---|---|
| **Stack** (`Stack.tsx` + `.css`) | Quiet "Built on production-grade open source" strip with 8 names: FastAPI, PostgreSQL, Celery, Redis, MinIO, Qdrant, React, Docker. Trust-build, no clutter. |
| **DeepDive** (`DeepDive.tsx` + `.css`) | Three alternating long-form blocks. Each: chip / title / paragraph / 4-item bullet list / mock visual aid. Block 1 (Structuring): mock CSV preview table `customers_q4.csv → v3` with email/name/age columns and color-coded quality pills. Block 2 (Labeling): a 4-layer stack RULE / LLM / PROP → OUT "14,238 rows · 96% labeled". Block 3 (Vision): mock query panel `"outdoor product on grass"` + 6-tile result grid with similarity scores. |
| **FAQ** (`FAQ.tsx` + `.css`) | 8-item accordion: how to install / where data goes / which LLM providers / Docker required / is it really free / remote server / why no accounts / how vs MLflow Label Studio W&B. First open by default. Inline `<code>` styling. |
| **Updates** (`Updates.tsx` + `.css`) | 5-card roadmap: Phase 1 (Ingest + Structure) shipped, Phase 2 (EDA + Rules) shipped, Phase 3 (AI Labels + Image) shipped, Phase 4 (Single-user pivot) **now**, Phase 5 (Docs + Tour) next. Each card has a green/purple/dim border. |

### Hero rewrite
Path: `src/website/components/Hero/Hero.tsx` + `.css`

The Hero went through several iterations:
1. Two-column with right-side 3D-tilt logo + left-aligned "Get started" Link
2. Single centered column, small spinning logo above headline, `<InstallPill />` centered
3. Compact 4-stage pipeline (Ingest → Structure → Label → Export) above headline (later removed)
4. **Current**: Single centered column. Top to bottom — eyebrow chip ("Now · Phase 4 — single-user pivot, pip-installable", links `#updates`) → 76×76 spinning Orchestraty mark with purple radial glow → two-line headline "An operating system / for your data." (second line is purple-to-cyan gradient via `background-clip: text`) → 19px subtext → `<InstallPill withFootnote={false} />` centered → chunky purple "▶ Take the tour" CTA → micro-claims row "Open source · Runs locally · No accounts · No telemetry" → soft fade to next section.

### ParticleCanvas upgrade
Path: `src/website/components/ParticleCanvas/ParticleCanvas.tsx`

User complaint: "the dots connecting at the back of the main section are very slow and isnt even noticed if scrolled to fast."

Cause: original code only drew the connection web between nearby particles **when the mouse was hovering**. Scroll past without hovering and you saw only static dots drifting at 0.25 px/frame.

Fixes:
1. **Connection web always renders** — O(n²) over ~260 particles, ~34k checks/frame, cheap on modern hardware. Constant constellation visible during scroll.
2. **BASE_SPEED = 1.4** (was 0.5) — ~3× faster baseline drift.
3. **DAMPING = 0.985** (was 0.92) — much less velocity loss per frame.
4. Constant gentle pull toward each particle's base velocity so motion never decays to zero.
5. **Density = area / 5000** (was `/7000`), capped at 260.
6. Faster alpha breathing (`0.008 * dir`, was `0.003`); alpha range 0.18–0.85 (was 0.1–0.8).
7. Brighter palette: web color now `rgba(140, 122, 230, opacity)` from `rgba(129, 140, 248, …)`.

Mouse-only effects (attraction force, lines from particles to cursor, soft glow) all still work on top of the always-on web.

### `/tour` page — cinematic walkthrough
Path: `src/website/pages/tour/Tour.tsx` + `.css`. Route: `/tour` (wired in `website-main.tsx`).

This went through two design iterations:

**v1 (deferred)** — generic stage cards with mock dashboards (drop zone, mock CSV table, etc.). The user said "looks a little basic and does not show any real detail it should be alittle detail and actual oreitned right like show the system for eg the data goes through this not the actaul data but a block showing data and then these processes happen and then it goes further then this then the user has these options like projects a mind map of the complete system what we have build till now."

**v2 (current)** — Architectural SVG diagrams per stage with traveling data blocks + visible user-option chips + right-side growing system mind map.

#### Page structure
- **Sticky header**: "← Back to site" + brand + "N / 8" progress.
- **Fixed left timeline** (visible ≥1080px): 8 numbered steps `01 Ingest` → `08 Export` with cyan dots for completed, purple-glow dot for active, dim for upcoming. Click any to anchor-jump.
- **Fixed right mind map** (visible ≥1280px): SVG-based "Your Orchestraty install" tree. Root node `core` always visible. 8 child branches (Datasets / Structuring / EDA / Rule sets / AI labels / Image pipeline / Workflows / Exports) start dim with dashed edges; as each section becomes active, the edge draws itself via `stroke-dashoffset` transition, the branch rect lights up (purple fill, brighter border), labels brighten. Bottom: "N of 8 components assembled" counter.
- **8 scroll-snapped sections** + a finale card.
- **IntersectionObserver** with `rootMargin: '-40% 0px -40% 0px'` tracks the active section and updates `active` state, which drives the timeline highlight, mind map fill-in, and `tour__section--active` / `tour__card--active` styling.
- **Animation restart**: each `StageDiagram` mounts with `key={`${id}-${active ? 'on' : 'off'}`}` so the entire SVG tree remounts when activation changes, restarting all CSS animations from the top.

#### Per-stage architecture diagrams (all SVG)

Common pattern per stage:
- `OptionsRow` above the SVG showing user choices as chips (`Source: [CSV] [JSON] [Parquet] [Image folder]` etc.).
- A `<svg viewBox>` containing labeled `<Node>` rectangles (purple-tinted rect + uppercase tag in JetBrains Mono + label in Montserrat 600), connected by `<Wire>` paths with `stroke-dasharray` self-drawing animation.
- Each wire optionally has a `<circle r=4>` that rides the path infinitely via `<animateMotion>`. These are the "data blocks" / "pulses" flowing through.

| Stage | Components shown | Visible user options |
|---|---|---|
| **01 Ingest** | `API POST /datasets` → `WORKER Validator` → `STORAGE MinIO` → `DB Postgres`, with feedback wire back to API response | Source: CSV / JSON / Parquet / Image folder |
| **02 Structure** | `DISPATCHER Structure run` → 3 parallel CELERY tasks (Type inferrer / Null+casing fix / Quality scorer) → `MERGE Version writer` → `DB v2` | Cleanup ops: trim / cast / fill-null / dedupe / case-fix |
| **03 EDA** | `API EDA dispatcher` → 3 parallel CELERY (Statistical profile / Embeddings ST / HDBSCAN clusterer) → 3 outputs (UI Charts / STORE Qdrant vectors / STORE Cluster table) | Compute: Profile / Embeddings / Clustering / Graphs |
| **04 Rules** | Two COND nodes (`amount > 10000` / `country in {US,CA,UK}`) → LOGIC `AND` → THEN `label = high_value` → PREVIEW `2,184 rows` | Operators: = / > / < / in / between / AND / OR |
| **05 AI Labels** | 4 LLM nodes (OpenAI / Anthropic / Groq / Ollama) → ROUTER `LiteLLM` → WORKER `Batch labeler` → DB `Labels` | Providers: OpenAI / Anthropic / Groq / Ollama / LM Studio |
| **06 Vision** | Index path: UPLOAD `Image upload` → META `EXIF + thumb` → MODEL `CLIP encoder` → STORE `Qdrant index`. Query path: QUERY `Text query` → MODEL `CLIP encoder` → UI `Ranked grid`. Both encoders share Qdrant. | Per image: EXIF / Thumbnail / CLIP / Cluster id / Search index |
| **07 Workflows** | DAG: `Workflow runner` → 2 parallel STEPs (Structure / EDA) → STEP `Label + AI fill` → 2 parallel STEPs (Review / Export) | Steps: Structure / EDA / Rules / AI labels / Review / Export |
| **08 Export** | 3 FORMAT nodes (CSV / JSON / Parquet) → STAMP `Lineage attacher` → HASH `SHA256 signer` → OUT `Download` | Format: CSV / JSON / Parquet / JSONL |

#### Animation choreography
- **Nodes** fade in + slide up via `node-in` keyframe (cubic-bezier ease-out, ~0.55s), staggered by 0.1–0.2s.
- **Wires** draw themselves via `stroke-dashoffset` 600→0 transition (`wire-draw` keyframe, 0.55s). Each wire's `animationDelay` is set so wires appear AFTER their originating node.
- **Pulses** ride each wire via `<animateMotion>` with `dur="3s"` and `repeatCount="indefinite"`, each pulse's `begin=` time staggered so the pipeline pulses look like sequential data flow rather than simultaneous fireworks.
- **Card**: dim/lift transition (`translateY(12px) opacity(0.55)` → `translateY(0) opacity(1)`) on activation, plus the border brightens.
- **Mind map**: each branch's `<path stroke-dasharray="240" stroke-dashoffset="240">` transitions to `stroke-dashoffset: 0` over 0.6s when its `.mindmap__branch--on` class flips on. Branch rect goes from `opacity: 0.35` to 1, fill/stroke brightens.

#### Finale section
"That's the whole system." headline (purple-to-cyan gradient), paragraph about all phases shipped, small `<InstallPill size="md" withFootnote={false}/>`, "Back to site" link.

---

## 9. Commands & operation surface

Three commands. Run all from the project root `C:\Users\prave\Orchestraty\ai-operating-system\`:

```powershell
.\setup.ps1              # backend + dashboard app at http://localhost:5173
.\setup.ps1 -Website     # backend + marketing site at http://localhost:5273
.\setup.ps1 -Fresh       # wipe ALL Docker volumes (DB / MinIO / queues / vectors) and start clean
.\stop.ps1               # stop frontend processes + docker compose down (volumes preserved)
```

`setup.ps1` step layout (5 numbered steps):
1. Cleanup stale processes on ports 5173 and 5273.
2. Either reuse containers, or with `-Fresh`: `docker compose down -v`.
3. `docker compose up -d`.
4. Wait for Postgres (`pg_isready`), Redis (`PING`), API (`GET /health`), MinIO (`9001`), Celery worker (running).
5. Ensure `node_modules` exists; `npm install` if first run.

Then it prints the banner and runs `npx vite` (or `npx vite --config vite.website.config.ts` with `-Website`) blocking. Ctrl-C stops the dev server; Docker keeps running.

`stop.ps1`:
1. Kill processes on ports 5173 + 5273.
2. `docker compose down` (volumes preserved).
3. Verify ports 5173, 5273, 8000, 5432, 6379 are free.

There is **no SuperAdmin Vite anymore** and no `-Refresh` flag. To rebuild Docker images: `docker compose -f infrastructure/docker-compose.yml up -d --build --force-recreate` manually.

---

## 10. The Edit / Write tool corruption bug (operational gotcha)

Multiple times during these phases, the Edit tool or Write tool on the Windows side **truncated files mid-write**, sometimes leaving trailing NUL bytes (~750 bytes), sometimes just clipping the file short. Confirmed instances:
- `core/api/image_routes.py` (truncated mid-function, recovered from `git show HEAD`)
- `frontend/Frontend/src/app/AppRouter.tsx` (truncated at line 57 of 76)
- `package.json` (truncated mid-dependencies block)
- `index.html` (filled with NUL bytes)
- `Hero.css` (regex cleanup left orphan keyframe fragments)
- `stop.ps1` (truncated mid-`Write-Host`)

The reliable workaround is to **write via the Linux bash side** (`/sessions/.../mnt/...`) using an atomic `tmp_path.write_text() + tmp_path.replace(target)` pattern. Every long-form write in Phases C, D, F was done this way.

Also: the Linux bash side **cannot delete files on the Windows-mounted filesystem** (Operation not permitted). To delete files, use the `mcp__cowork__allow_cowork_file_delete` tool first, which grants permission to the whole folder for the session.

---

## 11. Things the user explicitly deferred

| Item | Deferred to | Notes |
|---|---|---|
| Real tour mode (with sample dataset + actual pipeline execution) | Phase E (not started) | User said "we will work later on this" for all 3 clarifying questions: how many sample datasets / interactivity style / persisted vs ephemeral artifacts. The `/tour` page that does exist is a purely visual walkthrough; no real backend involvement. |
| Marketing donation provider wiring | Phase F (open) | The Pricing page and FinalCTA have placeholder "Donation channels coming soon" buttons. Provider TBD (GitHub Sponsors / Buy Me a Coffee / Open Collective / Polar). |
| Documentation site | Phase G (not started) | Navbar's "Docs" link currently anchors to `#get-started` as a placeholder. |
| The pip-install package itself | Future | User said: "I will let you know when that is to be done. For now we will stay on the setup run method." So `setup.ps1` + Docker Compose remains the install path. |
| Live `alembic upgrade head` test against populated Postgres | Local test only | Cannot be tested from the sandbox; user runs `.\setup.ps1 -Fresh` to verify. |

---

## 12. Open questions the user hasn't decided

These came up during the work and weren't resolved:

1. **Donation provider** — which platform to link to from Pricing + Support panel.
2. **Tour sample dataset shape** — when Phase E is built: one CSV, three datasets, or generated-on-demand.
3. **Phase E tour interactivity** — auto-advance vs click-next vs scroll-snap.
4. **What "Docs" should link to** — own docs site (Phase G build) or GitHub README.
5. **Whether the localhost app's `/admin`, `/audit`, `/auth` route comments / Updates section copy** should reflect "Phase 4 done" or stay future-tense pending user confirmation. They currently say Phase 4 is the active phase.

---

## 13. Verification & test surface

To re-verify the system end-to-end:

```powershell
cd C:\Users\prave\Orchestraty\ai-operating-system

# Clean start
.\stop.ps1
.\setup.ps1 -Fresh

# After browser auto-opens at :5173, in a second PowerShell:
Invoke-RestMethod http://localhost:8000/health
docker exec orchestraty-api alembic current
# expect: f74e9b5c6d83 (the Phase D drop migration)

# Confirm schema is the new shape
docker exec orchestraty-postgres psql -U orchestraty -d orchestraty -c "\dt" | Select-String -Pattern "users|audit_logs|dataset_permissions"
# expect: no matches

docker exec orchestraty-postgres psql -U orchestraty -d orchestraty -c "\d datasets"
# expect: no created_by, no org_id

# Marketing site
.\stop.ps1
.\setup.ps1 -Website
# browser auto-opens at :5273
# verify Hero, scroll through Stack, Features, HowItWorks, DeepDive, UseCases, Integrations, FAQ, Updates, FinalCTA
# click "Take the tour" → /tour, scroll through 8 architectural stages
```

To re-verify TypeScript health from the project root:
```powershell
cd frontend/Frontend
npx tsc -b --pretty false
# expect exit code 0
```

---

## 14. Codebase metrics (after Phase D + F)

Backend Python:
- `core/api/` — 9 files (was 12)
- `core/models/` — 5 files (was 8)
- `core/services/` — 3 files (was 5)
- `core/middleware/` — empty (rbac.py gone)
- `app.py` — ~280 lines (was ~340)
- Routes: roughly 1,000 lines lighter than HEAD

Frontend TypeScript:
- `src/app/` — 30 files
- `src/website/` — 35 files (grew with Stack, DeepDive, FAQ, Updates, InstallPill, Tour)
- `src/shared/` — 15 files
- `src/superadmin/` — gone (was 9)

Migrations: 5 in chain — `b30963890f73 → c41a8b2e1f90 → d52b7c3f2a81 → e63d8a4f5b92 → f74e9b5c6d83`.

Vite entries: 2 (was 3). Configs: `vite.config.ts`, `vite.website.config.ts`.

Python deps removed: `passlib[bcrypt]`, `python-jose[cryptography]`, `bcrypt`.

---

## 15. If I were to pick up the next task, where would I start?

In rough priority order:

1. **Live test the system end-to-end on the user's machine.** Phase D migration has not been run against a populated Postgres yet. If anything breaks, it'll be there.
2. **Phase E — real tour mode.** The user wants this; only deferred until decisions on data shape and interactivity. The `/tour` page already proves the visual shell works; what's needed is hooking it up to a real sample dataset that flows through real endpoints. Three pending decisions need answering first (see §11 + §12).
3. **Phase G — documentation site.** Currently the Navbar "Docs" link is a placeholder anchor. A real docs site gives the FAQ and Updates sections somewhere to deep-link to. Could be MDX-based, built into the marketing site, or hosted separately.
4. **Donation provider integration.** Once the user picks GitHub Sponsors / Open Collective / etc., the placeholder buttons in `Pricing.tsx` and `FinalCTA.tsx` light up.
5. **The actual `pip install orchestraty` package.** Requires `pyproject.toml`, a console-entrypoint `orchestraty` command that wraps `docker compose`, and PyPI publishing. User said this is later.

---

## 16. Glossary of file paths (cheat sheet for the next session)

| Symbol | Path |
|---|---|
| Backend root | `core/` |
| Models | `core/models/` |
| Routes | `core/api/` |
| Services | `core/services/` |
| Synthetic user definition | `core/services/auth_service.py` (lines 27–50) |
| Phase D drop migration | `alembic/versions/f74e9b5c6d83_drop_users_orgs_audit_logs.py` |
| Alembic env (do not break) | `alembic/env.py` |
| FastAPI entry | `app.py` |
| Ingestion routes (out-of-`core/`) | `data-intelligence-system/ingestion/api/routes.py` |
| Ingestion service | `data-intelligence-system/ingestion/services/dataset_service.py` |
| Ingestion schemas | `data-intelligence-system/ingestion/api/schemas.py` |
| Labeling rule store | `data-intelligence-system/labeling/rule_store.py` |
| Celery tasks | `data_intelligence/tasks/*.py` |
| Backend deps | `requirements.txt` |
| Setup script | `setup.ps1` (project root) |
| Stop script | `stop.ps1` (project root) |
| Docker compose | `infrastructure/docker-compose.yml` |
| App entry HTML | `frontend/Frontend/index.html` |
| Website entry HTML | `frontend/Frontend/website.html` |
| Vite app config | `frontend/Frontend/vite.config.ts` |
| Vite website config | `frontend/Frontend/vite.website.config.ts` |
| App entry TS | `frontend/Frontend/src/app/main.tsx` |
| Website entry TS | `frontend/Frontend/src/website/website-main.tsx` |
| App router | `frontend/Frontend/src/app/AppRouter.tsx` |
| Dashboard layout | `frontend/Frontend/src/app/layouts/DashboardLayout.tsx` |
| Website composition | `frontend/Frontend/src/website/WebsiteApp.tsx` |
| Install pill (reusable) | `frontend/Frontend/src/website/components/InstallPill/` |
| `/tour` page | `frontend/Frontend/src/website/pages/tour/Tour.tsx` + `Tour.css` |
| Shared design tokens CSS | `frontend/Frontend/src/shared/index.css` |
| Shared API axios modules | `frontend/Frontend/src/shared/api/` |

---

End of handoff. If you're picking this up: open this file in any editor, read whatever section you need, and start from §15.
