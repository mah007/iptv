# syntax=docker/dockerfile:1.7
# Control-plane image: web (Gunicorn + Uvicorn workers), Celery worker and beat.
# Targets: `dev` (dev dependencies, sources bind-mounted) and `runtime` (production).

ARG PYTHON_IMAGE=python:3.13.16-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.22

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS deps
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project
# The dev container runs as the host user so files it writes stay editable.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
EXPOSE 8000
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod
# Apply pending distro security fixes; drop pip, which the runtime never uses
# (its vendored urllib3/msgpack/setuptools are scanner findings and attack surface).
RUN apt-get update \
    && apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes pip \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY backend/ /app/
# collectstatic only needs settings to import; these values never leave this layer.
RUN DJANGO_SECRET_KEY=build POSTGRES_DB=build POSTGRES_USER=build POSTGRES_PASSWORD=build \
    REDIS_STATE_PASSWORD=build REDIS_CACHE_PASSWORD=build MEILI_MASTER_KEY=build \
    python manage.py collectstatic --noinput \
    && python -m compileall -q /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "apps.core.healthcheck", "web"]
# Gunicorn reads WEB_CONCURRENCY for the worker count (SPEC §13: 2×CPU+1 in prod).
# No control socket: the container is managed by restarts, and /app is read-only to uid 10001.
CMD ["gunicorn", "config.asgi:application", "--worker-class", "uvicorn_worker.UvicornWorker", "--bind", "0.0.0.0:8000", "--no-control-socket", "--access-logfile", "-"]
