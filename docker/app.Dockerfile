# syntax=docker/dockerfile:1.7
# Control-plane image: web (Gunicorn + Uvicorn workers), Celery worker and beat.
# Targets: `dev` (dev dependencies, FFmpeg and rclone, sources bind-mounted), `runtime`
# (production web and beat), `media` (runtime plus FFmpeg: worker, watcher, transcoder)
# and `importer` (runtime plus rclone: the importer, ADR-0025).

# Base images pinned by digest as well as tag (G-39, ADR-0020).
ARG PYTHON_IMAGE=python:3.13.16-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc
# FFmpeg (ADR-0009): jellyfin-ffmpeg's Debian 13 package, matching the python image's
# Debian. GPL, so it only ever runs as a separate process (ffprobe/ffmpeg/vainfo), never
# linked or imported. It bundles the Intel iHD VA-API driver and the oneVPL GPU runtime
# (VAAPI and QSV); NVENC comes from the NVIDIA container runtime. amd64 only.
ARG FFMPEG_DEB_URL=https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v8.1.3-1/jellyfin-ffmpeg8_8.1.3-1-trixie_amd64.deb
ARG FFMPEG_DEB_SHA256=fbef9f81a53e175194e3f67832618a86111f7bd37197df21a5d748c34289f9c0
# rclone (ADR-0025): MIT, one static Go binary, run only as a separate process by the
# importer. The release zip, installed only after its SHA-256 (the release's SHA256SUMS)
# matches.
ARG RCLONE_ZIP_URL=https://github.com/rclone/rclone/releases/download/v1.75.2/rclone-v1.75.2-linux-amd64.zip
ARG RCLONE_ZIP_SHA256=349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702

FROM ${UV_IMAGE} AS uv

# The package as downloaded; the stages that install it verify the checksum first.
FROM scratch AS ffmpeg-deb
ARG FFMPEG_DEB_URL
ADD ${FFMPEG_DEB_URL} /jellyfin-ffmpeg.deb

FROM scratch AS rclone-zip
ARG RCLONE_ZIP_URL
ADD ${RCLONE_ZIP_URL} /rclone.zip

# The binary out of the checked zip (Python's zipfile: the slim image has no unzip).
FROM ${PYTHON_IMAGE} AS rclone
ARG RCLONE_ZIP_SHA256
RUN --mount=type=bind,from=rclone-zip,source=/rclone.zip,target=/tmp/rclone.zip \
    echo "${RCLONE_ZIP_SHA256}  /tmp/rclone.zip" | sha256sum -c - \
    && python -c "import shutil, zipfile; z = zipfile.ZipFile('/tmp/rclone.zip'); \
name = next(n for n in z.namelist() if n.endswith('/rclone')); \
shutil.copyfileobj(z.open(name), open('/usr/local/bin/rclone', 'wb'))" \
    && chmod 0755 /usr/local/bin/rclone \
    && /usr/local/bin/rclone version > /dev/null

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
ARG FFMPEG_DEB_SHA256
# FFmpeg in dev too: the worker probes media, and the FFmpeg integration tests run in
# `make test` (apps/media/tests/test_integration.py).
RUN --mount=type=bind,from=ffmpeg-deb,source=/jellyfin-ffmpeg.deb,target=/tmp/jellyfin-ffmpeg.deb \
    echo "${FFMPEG_DEB_SHA256}  /tmp/jellyfin-ffmpeg.deb" | sha256sum -c - \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/jellyfin-ffmpeg.deb \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/lib/jellyfin-ffmpeg/ffprobe \
        /usr/lib/jellyfin-ffmpeg/vainfo /usr/local/bin/ \
    && ffprobe -hide_banner -version > /dev/null
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project
# rclone in dev too: the importer runs on this image, and its tests run real transfers.
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
# The dev container runs as the host user so files it writes stay editable.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
EXPOSE 8000
# No access log: it prints raw paths, and Xtream paths carry credentials.
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--reload", "--no-access-log"]

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
# Transcoding profiles (apps/media/profiles.py resolves /streaming/ffmpeg/profiles.yaml).
COPY streaming/ffmpeg/profiles.yaml /streaming/ffmpeg/profiles.yaml
# collectstatic only needs settings to import; these values never leave this layer.
RUN DJANGO_SECRET_KEY=build POSTGRES_DB=build POSTGRES_USER=build POSTGRES_PASSWORD=build \
    REDIS_STATE_PASSWORD=build REDIS_CACHE_PASSWORD=build MEILI_MASTER_KEY=build \
    FIELD_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
    python manage.py collectstatic --noinput \
    && python -m compileall -q /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "apps.core.healthcheck", "web"]
# config/gunicorn_conf.py: Uvicorn workers (WEB_CONCURRENCY, SPEC §13: 2×CPU+1 in prod),
# no control socket, no access log (RequestLogMiddleware logs redacted requests),
# and the Prometheus multiprocess directory.
CMD ["gunicorn", "--config", "config/gunicorn_conf.py", "config.asgi:application"]

# Worker, watcher and (slice 3) transcoder: the runtime plus FFmpeg. Compose gives each
# service its command and healthcheck.
FROM runtime AS media
ARG FFMPEG_DEB_SHA256
USER root
RUN --mount=type=bind,from=ffmpeg-deb,source=/jellyfin-ffmpeg.deb,target=/tmp/jellyfin-ffmpeg.deb \
    echo "${FFMPEG_DEB_SHA256}  /tmp/jellyfin-ffmpeg.deb" | sha256sum -c - \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/jellyfin-ffmpeg.deb \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/lib/jellyfin-ffmpeg/ffprobe \
        /usr/lib/jellyfin-ffmpeg/vainfo /usr/local/bin/ \
    && ffprobe -hide_banner -version > /dev/null
USER 10001:10001
HEALTHCHECK NONE
CMD ["celery", "-A", "config", "worker", "--queues", "default,scan,metadata,images,notify", "--loglevel", "INFO"]

# The importer (ADR-0025): the runtime plus rclone. It consumes only the `import` queue
# and is the one service that mounts the media libraries read-write.
FROM runtime AS importer
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
USER 10001:10001
HEALTHCHECK NONE
CMD ["celery", "-A", "config", "worker", "--queues", "import", "--pool", "threads", "--concurrency", "4", "--hostname", "importer@%h", "--loglevel", "INFO"]
