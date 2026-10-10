# Handy shortcuts. Run `make` to list them. Requires Docker Compose v2.
.DEFAULT_GOAL := help
COMPOSE := docker compose
PROD    := docker compose -f docker-compose.yml -f docker-compose.prod.yml

# `start` is `up` without --build: use it when Docker Hub is unreachable (build asks it about base images).
.PHONY: help env up start down clean logs ps restart migrate superuser shell test test-mysql lint \
        frontend prod-up prod-down prod-logs

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example (never overwrites an existing one)
	@test -f .env || (cp .env.example .env && echo "Created .env - edit the secrets before first use")

up: env ## Start the development stack (MySQL, Redis, API, worker) in the background
	$(COMPOSE) up -d --build

start: env ## Start the development stack WITHOUT rebuilding images (works offline / when Docker Hub is blocked)
	$(COMPOSE) up -d --no-build

down: ## Stop the stack (data is kept)
	$(COMPOSE) down

clean: ## Stop the stack AND delete the database volume
	$(COMPOSE) down -v

logs: ## Follow API and worker logs
	$(COMPOSE) logs -f backend worker

ps: ## Show service status and health
	$(COMPOSE) ps

restart: ## Restart API and worker (needed after changing Celery task code)
	$(COMPOSE) restart backend worker

migrate: ## Apply database migrations
	$(COMPOSE) run --rm migrate

superuser: ## Create a Django admin user
	$(COMPOSE) exec backend python manage.py createsuperuser

shell: ## Open a Django shell in the API container
	$(COMPOSE) exec backend python manage.py shell

test: ## Run the backend test suite in the container (SQLite + real Redis)
	$(COMPOSE) exec -T -e DATABASE_URL=sqlite:////tmp/test.sqlite3 -e TEST_REDIS_URL=redis://redis:6379/15 \
		backend sh -c "pip install -q -r requirements-dev.txt && python -m pytest -p no:cacheprovider"

test-mysql: ## Run the backend test suite against the MySQL container
	$(COMPOSE) exec -T -e TEST_REDIS_URL=redis://redis:6379/15 backend sh -c \
		'pip install -q -r requirements-dev.txt && DATABASE_URL="mysql://root:$$MYSQL_ROOT_PASSWORD@db:3306/edgeshield_db" python -m pytest -p no:cacheprovider'

lint: ## Lint the backend with ruff
	$(COMPOSE) exec -T backend sh -c "pip install -q ruff && ruff check ."

frontend: ## Run the Vue dev server on http://localhost:5173
	cd frontend && npm install && npm run dev

prod-up: env ## Start the production-like stack (nginx + gunicorn) on http://localhost:8080
	$(PROD) up -d --build

prod-down: ## Stop the production-like stack
	$(PROD) down

prod-logs: ## Follow logs of the production-like stack
	$(PROD) logs -f
