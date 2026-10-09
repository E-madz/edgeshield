# EdgeShield

A **control plane for a CDN / edge platform**: customers register their domains and origin servers, define
cache and rate-limit policies, and purge cached content. EdgeShield stores and validates this configuration and
exposes it over a REST API (plus a small Vue dashboard).

> **Scope, stated honestly.** EdgeShield is the *control plane* only. It does not proxy real user traffic
> (that would be the *data plane*, e.g. nginx/Envoy nodes at the edge). To make the policies observable it ships
> **simulators** that apply the stored policies to fake traffic using real implementations of the underlying
> algorithms (LRU cache, token bucket, sliding window) and a real Redis.

## Features

| Area | What it does |
|---|---|
| **Auth** | Email + password sign-up, JWT access/refresh tokens, per-IP throttling on login and sign-up |
| **Domains & origins** | CRUD with per-user ownership, pagination, search, ordering, validation (hostnames, IPs, ports) |
| **Cache policy** | TTLs, stale-while-revalidate, query-string handling, bypass paths; **cache simulator** (HIT / MISS / STALE / BYPASS) |
| **Rate-limit policy** | Token bucket or sliding window, per IP or global, block or monitor-only; **sandbox** backed by Redis |
| **Config snapshot** | `GET /api/domains/{id}/config/` returns what a data plane would consume, with a version |
| **Purge jobs** | Asynchronous purge (everything, paths, `/prefix/*`) via Celery, with idempotency keys, retries and job status |
| **Operations** | Liveness/readiness endpoints, structured logging, Docker images (non-root), nginx reverse proxy, CI |

## Architecture

```mermaid
flowchart LR
    B[Browser] -->|HTTP| N[nginx]
    N -->|"/ (static files)"| V[Vue app]
    N -->|"/api, /admin"| G[Django + DRF<br/>gunicorn]
    G --> M[(MySQL 8)]
    G -->|"limiter state, throttling,<br/>simulated edge cache"| R[(Redis)]
    G -->|enqueue purge job| R
    W[Celery worker] -->|consume jobs| R
    W --> M
    W -->|purge objects| R
```

**Request path** (`GET /api/domains/`): browser → nginx (proxies `/api`) → gunicorn (WSGI) → Django middleware
(security, CORS, sessions, auth…) → URL resolver → DRF view (JWT authentication, permissions, pagination) → ORM →
MySQL → serializer → JSON response, passing back through the middleware.

**Why two Redis uses and a database for job state?** MySQL is the source of truth (domains, policies, purge-job
status). Redis holds data that is fast-changing and disposable: rate-limit counters, the simulated edge cache,
the Celery queue and throttle counters (separate Redis databases 0, 1, 2).

## Tech stack

Python 3.13 · Django 5.2 LTS · Django REST framework · SimpleJWT · MySQL 8 · Redis 7 · Celery 5 · gunicorn ·
WhiteNoise · Vue 3 · Vite · Pinia · Vue Router · nginx · Docker Compose · GitHub Actions · pytest · ruff

## Quick start (Docker, development)

Requires Docker with Compose v2.

```bash
git clone <this repo> && cd edgeshield
make up                 # creates .env from .env.example, builds and starts MySQL, Redis, API, worker
make frontend           # in a second terminal: Vue dev server on http://localhost:5173
```

Open <http://localhost:5173>, register an account, add a domain and explore. The API itself is on
<http://localhost:8000/api/health/>. Edit the secrets in `.env` before first use (`make up` copies the example).

Useful commands: `make logs`, `make ps`, `make test`, `make lint`, `make superuser`, `make down` (keep data),
`make clean` (delete the database volume). Run `make` for the full list.

> Slow or restricted network? Set `PIP_INDEX_URL` and/or `NPM_REGISTRY` in `.env` to a mirror; they are used when
> building the images.

## Production-like run (nginx + gunicorn on your machine)

```bash
make prod-up            # builds the Vue app into an nginx image, runs everything behind nginx
open http://localhost:8080
make prod-down
```

Differences from development: the API is **not** published, nginx is the only entry point (single origin, so no
CORS), `DEBUG=False`, the app runs as an unprivileged user, migrations run once in a dedicated `migrate` job, and
the containers have health checks. HTTPS-only settings (SSL redirect, secure cookies, HSTS) are switched off for
plain-HTTP localhost via `DJANGO_USE_HTTPS=False`; on a real domain behind TLS leave it at its default `True`.

## Running without Docker for the app itself

Run only the databases in Docker and the Python/Node processes on your host:

```bash
docker compose up -d db redis                      # published on 127.0.0.1:3307 and 127.0.0.1:6380

cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt                # mysqlclient needs libmysqlclient-dev + pkg-config
export DATABASE_URL="mysql://<MYSQL_USER>:<MYSQL_PASSWORD>@127.0.0.1:3307/edgeshield_db"
export REDIS_URL=redis://127.0.0.1:6380/0 CELERY_BROKER_URL=redis://127.0.0.1:6380/1 CACHE_URL=redis://127.0.0.1:6380/2
export DJANGO_SECRET_KEY=dev-secret DJANGO_DEBUG=True
python manage.py migrate && python manage.py runserver        # API on :8000
celery -A config worker --loglevel=info                        # in another terminal: background jobs
cd ../frontend && npm install && npm run dev                   # UI on :5173
```

No MySQL at all? `export DATABASE_URL=sqlite:///db.sqlite3` works for development (Redis is still needed for the
rate-limit sandbox, purge jobs and login throttling).

## Using the app: a 3-minute tour

1. **Register** and you are logged in (the access token lives in memory, the refresh token in `localStorage`).
2. **Add a domain** (`www.example.com`), open it, **add an origin** (`203.0.113.10:443`).
3. **Cache policy**: set a TTL and bypass path, save, then run the **simulator** to see HIT/MISS/STALE.
4. **Rate limiting**: enable it (e.g. 5 requests per 3600 s), save, then use the **sandbox**: 10 requests give 5
   green and 5 red squares; wait and send again to watch tokens refill.
5. **Purge**: *Add sample objects*, purge `/blog/*`, and watch the job go `pending → succeeded` while the object
   list shrinks. Double-clicking *Purge* does not create two jobs thanks to the idempotency key.

## API overview

All routes are under `/api/` and need `Authorization: Bearer <access token>` unless noted.

| Method & path | Purpose |
|---|---|
| `GET /health/live/`, `GET /health/` *(public)* | Liveness; readiness (checks the database) |
| `POST /auth/register/`, `/auth/login/`, `/auth/refresh/` *(public)* | Account and tokens |
| `GET /auth/me/` | Current user |
| `GET, POST /domains/` · `GET, PATCH, DELETE /domains/{id}/` | Domains (`?search=`, `?ordering=`, `?page=`, `?page_size=`) |
| `GET, POST /domains/{id}/origins/` · `…/origins/{oid}/` | Origins of a domain |
| `GET, PUT, PATCH /domains/{id}/cache-policy/` | Cache policy (created with defaults on first access) |
| `POST /domains/{id}/cache-policy/simulate/` | Replay `(path, time)` requests against an LRU cache |
| `GET, PUT, PATCH /domains/{id}/rate-limit-policy/` | Rate-limit policy |
| `POST, DELETE /domains/{id}/rate-limit-policy/check/` | Redis-backed sandbox; reset a client's state |
| `GET /domains/{id}/config/` | Versioned snapshot for a data plane |
| `POST, GET /domains/{id}/purges/` · `GET …/purges/{job}/` | Purge jobs (`Idempotency-Key` header supported) |
| `GET, POST /domains/{id}/cache-objects/` | Demo helper: seed/list the simulated edge cache |

Errors are JSON. Notable status codes: `202` (job accepted), `400` (validation), `401`, `404` (also for other users'
objects, so ids do not leak), `409` (idempotency key reused with a different body), `429` (throttled / too many
active purges, with `Retry-After`), `503` (Redis or the queue is down).

## Configuration

Everything comes from environment variables (see `.env.example`); no secret is committed.

| Variable | Meaning |
|---|---|
| `DJANGO_SECRET_KEY` | Required. Signs sessions and JWTs |
| `DJANGO_DEBUG` | `False` in production |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames |
| `DJANGO_USE_HTTPS` | Default `True` when not debugging: SSL redirect, secure cookies, HSTS |
| `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS` | Origins for the admin (HTTPS) and for a separately hosted frontend |
| `DATABASE_URL` | e.g. `mysql://user:pass@host:3306/db` or `sqlite:///db.sqlite3` |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CACHE_URL` | Redis databases 0 (limiters), 1 (Celery), 2 (throttling) |
| `AUTH_THROTTLE_RATE` | Login/register attempts per IP, default `10/min` |
| `TRUSTED_PROXY_COUNT` | Number of reverse proxies in front of Django (1 behind nginx) so the real client IP is used |

## Testing and quality

```bash
make test         # backend tests inside the container (SQLite + real Redis)
make test-mysql   # same tests against the MySQL container
make lint         # ruff
```

With a local virtualenv instead of Docker:
`cd backend && DJANGO_SECRET_KEY=test DATABASE_URL=sqlite:///test.sqlite3 pytest`. Two tests that need a real Redis
are skipped unless `TEST_REDIS_URL` points to one (e.g. `redis://localhost:6379/15`).

The suite (200+ tests) covers the API, ownership isolation, validation matrices, query counts (guarding against
N+1 queries), the algorithms with an injected clock, **atomicity of the Redis Lua scripts under 200 concurrent
requests**, Celery retries and idempotency. CI (`.github/workflows/ci.yml`) runs lint, `manage.py check --deploy`,
a migrations-in-sync check, the tests on **MySQL 8 + Redis**, the frontend build and the Docker image builds.

## Project layout

```
backend/
  config/      settings, URLs, Celery app
  accounts/    custom user (email login), register/login/me
  domains/     domains, origins, policies, purge jobs (models, API, tasks)
  edge/        framework-free logic: LRU cache, rate limiters, cache simulator, edge cache store
  core/        health checks, pagination
frontend/      Vue 3 app, nginx.conf, production Dockerfile
docker-compose.yml           services (db, redis, migrate, backend, worker)
docker-compose.override.yml  development additions (live reload, published ports)
docker-compose.prod.yml      production-like run (adds nginx, hardens settings)
docs/interview-notes.fa.md   design decisions and interview Q&A (Persian)
```

## Design decisions worth knowing

- **Custom user model with email login** from the first migration (swapping it later is painful).
- **Ownership by scoping every queryset** to `request.user`; foreign objects return 404, not 403.
- **Domain names are globally unique**, enforced by a database `UNIQUE` index (the serializer check alone races).
- **N+1 avoided** with `annotate(Count)` and `prefetch_related`, with tests asserting a constant query count.
- **Atomic rate limiting** with Lua scripts: read-modify-write inside Redis cannot interleave.
- **Purge jobs**: persisted first, enqueued `on_commit`, executed by an idempotent task with `acks_late`,
  exponential backoff with jitter, and a `UNIQUE(domain, idempotency_key)` constraint behind the HTTP replay logic.
- **Migrations as a one-shot job** in Compose instead of inside every app container.
- **Time is injected** into the limiters and the cache simulator, so time-dependent behaviour is tested without sleeping.

## Known limitations / roadmap

- No real data plane: policies are enforced only by the simulators.
- Logout is client-side only: refresh tokens are not rotated or blacklisted yet.
- No DNS-based domain ownership verification; no email verification or password reset.
- No metrics/tracing (only logs and health endpoints); no Kubernetes manifests.
- Redis is a single instance without persistence by design (counters and queue are disposable).
