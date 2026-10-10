FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

# Optional PyPI mirror for slow or restricted networks, e.g. in .env:
#   PIP_INDEX_URL=https://mirror.example.com/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

# Build tools are needed to compile mysqlclient; the runtime library stays.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential pkg-config default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user: a compromised process must not be root in the container.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt .
# The cache mount keeps downloaded packages between builds, so a retry after a
# network failure does not start from zero.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY --chown=app:app . .

# Collect admin static files into the image (dummy values: collectstatic touches neither DB nor secrets).
RUN DJANGO_SECRET_KEY=build-only DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    python manage.py collectstatic --noinput \
    && chown -R app:app /app/staticfiles

USER app
EXPOSE 8000
# gunicorn (WSGI) instead of runserver: runserver is for development only.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
