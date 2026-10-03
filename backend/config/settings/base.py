"""Settings shared by every environment. `dev`, `test` and `prod` import from here."""

from pathlib import Path

from kombu import Queue

from config.env import env, env_int, env_list

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
APP_VERSION = env("APP_VERSION", "0.1.0-dev")

# --- Hosts and routing --------------------------------------------------------
# Traefik forwards only the public hosts below to Django. Each host gets its own
# URLconf (apps.core.middleware.HostURLConfMiddleware); any other allowed host,
# such as the in-network name the edge uses, falls through to ROOT_URLCONF,
# which holds internal-only endpoints.
DOMAIN = env("DOMAIN", "localhost")
API_HOST = env("API_HOST", f"api.{DOMAIN}")
TV_HOST = env("TV_HOST", f"tv.{DOMAIN}")
INTERNAL_HOSTS = env_list("INTERNAL_HOSTS", ["web", "localhost", "127.0.0.1"])
ALLOWED_HOSTS = [API_HOST, TV_HOST, *INTERNAL_HOSTS]
HOST_URLCONFS = {
    API_HOST: "config.urls_api",
    TV_HOST: "config.urls_xtream",
}
ROOT_URLCONF = "config.urls_internal"

# --- Applications -------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "apps.core",
    "apps.accounts",
    "apps.billing",
    "apps.catalog",
    "apps.library",
    "apps.metadata",
    "apps.media",
    "apps.playback",
    "apps.xtream_api",
    "apps.live",
    "apps.engagement",
    "apps.search",
    "apps.notifications",
    "apps.audit",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.HostURLConfMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = "config.wsgi.application"

# The custom user model exists from the first migration: swapping it later
# requires rebuilding the database (ADR-0002).
AUTH_USER_MODEL = "accounts.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Data stores ----------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("POSTGRES_HOST", "postgres"),
        "PORT": env_int("POSTGRES_PORT", default=5432),
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        # Persistent connections are off under ASGI (Django's guidance);
        # pooling is decided with the load tests in M15.
        "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", default=0),
        "CONN_HEALTH_CHECKS": True,
    }
}


def _redis_url(host_var: str, password_var: str, default_host: str, db: int) -> str:
    return f"redis://:{env(password_var)}@{env(host_var, default_host)}:6379/{db}"


# redis-state: noeviction + AOF. Sessions, concurrency slots, entitlements, kicks,
# Celery broker. Nothing here may be evicted.
REDIS_STATE_URL = _redis_url("REDIS_STATE_HOST", "REDIS_STATE_PASSWORD", "redis-state", 0)
CELERY_BROKER_URL = _redis_url("REDIS_STATE_HOST", "REDIS_STATE_PASSWORD", "redis-state", 1)
# redis-cache: allkeys-lru. Only data that can be recomputed.
REDIS_CACHE_URL = _redis_url("REDIS_CACHE_HOST", "REDIS_CACHE_PASSWORD", "redis-cache", 0)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "KEY_PREFIX": "iptv",
    }
}

MEILI_URL = env("MEILI_URL", "http://meilisearch:7700")
MEILI_MASTER_KEY = env("MEILI_MASTER_KEY")

# --- Celery -------------------------------------------------------------------
# Queues from SPEC §13. `worker` consumes the general queues; transcoders
# subscribe only to the transcode.* queue matching their hardware (M8).
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_QUEUES = tuple(
    Queue(name)
    for name in (
        "default",
        "scan",
        "metadata",
        "images",
        "notify",
        "transcode.cpu",
        "transcode.qsv",
        "transcode.vaapi",
        "transcode.nvenc",
    )
)
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULE = {
    "core-heartbeat": {
        "task": "apps.core.tasks.heartbeat",
        "schedule": 30.0,
        "options": {"expires": 25},
    },
}

# --- I18n -----------------------------------------------------------------------
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("ar", "العربية")]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Security baseline (prod.py tightens transport security) ------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
}
