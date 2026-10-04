"""Settings shared by every environment. `dev`, `test` and `prod` import from here."""

from pathlib import Path

from celery.schedules import crontab
from kombu import Exchange, Queue

from apps.core.logs import configure_structlog, logging_config, parse_log_format
from config.env import env, env_bool, env_int, env_list
from config.origins import origin, origins

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
APP_VERSION = env("APP_VERSION", "0.1.0-dev")

# --- Hosts and routing --------------------------------------------------------
# Traefik forwards only the public hosts below to Django. Each host gets its own
# URLconf (apps.core.middleware.HostURLConfMiddleware); any other allowed host,
# such as the in-network name the edge uses, falls through to ROOT_URLCONF,
# which holds internal-only endpoints. The admin SPA and the portal call their
# API on their own host (`/api` is routed to Django there), ADR-0004.
DOMAIN = env("DOMAIN", "localhost")
API_HOST = env("API_HOST", f"api.{DOMAIN}")
TV_HOST = env("TV_HOST", f"tv.{DOMAIN}")
ADMIN_HOST = env("ADMIN_HOST", f"admin.{DOMAIN}")
APP_HOST = env("APP_HOST", f"app.{DOMAIN}")
INTERNAL_HOSTS = env_list("INTERNAL_HOSTS", ["web", "localhost", "127.0.0.1"])
ALLOWED_HOSTS = [API_HOST, TV_HOST, ADMIN_HOST, APP_HOST, *INTERNAL_HOSTS]
HOST_URLCONFS = {
    API_HOST: "config.urls_api",
    TV_HOST: "config.urls_xtream",
    ADMIN_HOST: "config.urls_admin",
    APP_HOST: "config.urls_portal",
}
ROOT_URLCONF = "config.urls_internal"
# The media edge (nginx-stream) serves artwork, and from slice 3 media, on its own host.
MEDIA_HOST = env("MEDIA_HOST", f"media.{DOMAIN}")

# Where browsers load the admin SPA and the portal from. Dev: plain HTTP on
# HTTP_PORT (8080 when port 80 is taken); prod.py switches to https on 443.
PUBLIC_SCHEME = env("PUBLIC_SCHEME", "http")
PUBLIC_PORT = env_int("PUBLIC_PORT", default=env_int("HTTP_PORT", default=80))
CSRF_TRUSTED_ORIGINS = origins(PUBLIC_SCHEME, [ADMIN_HOST, APP_HOST], PUBLIC_PORT)
# Public base URL of the edge: artwork is `{MEDIA_BASE_URL}/images/...`.
MEDIA_BASE_URL = env("MEDIA_BASE_URL", origin(PUBLIC_SCHEME, MEDIA_HOST, PUBLIC_PORT))

# --- Applications -------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "django_prometheus",
    "axes",
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
    # First and last: request counts and latency by view (no paths in labels).
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    # Early, so every response (even a 400 for a bad Host) gets a request id and a log line.
    "apps.core.middleware.RequestLogMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.HostURLConfMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # After authentication: turns a sign-in that django-axes locked out into a 429.
    "axes.middleware.AxesMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
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

# --- Authentication (SPEC §11; ADR-0006) -----------------------------------------
# django-axes first: it refuses sign-ins from a locked-out username + IP pair
# before any password is checked.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
# Admin passwords: Argon2id (Django's Argon2 hasher), PBKDF2 kept to read old hashes.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# Admin sign-in lockouts, per username and client IP: 5 failures (wrong
# password or wrong MFA code) lock the pair for 15 minutes, doubling with each
# further failure up to 24 hours. Never permanent (apps.accounts.lockout).
AXES_FAILURE_LIMIT = 5
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_COOLOFF_TIME = "apps.accounts.lockout.cool_off"
AXES_RESET_ON_SUCCESS = True
AXES_CLIENT_IP_CALLABLE = "apps.accounts.lockout.axes_client_ip"
AXES_LOCKOUT_CALLABLE = "apps.accounts.lockout.lockout_response"
# Successful sign-ins are audited by us (auth.login); axes keeps failures only.
AXES_DISABLE_ACCESS_LOG = True
# Fernet key(s) for secrets stored in the database (admin TOTP seeds),
# comma-separated, newest first (apps.accounts.crypto).
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")

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

# --- Media storage (docs/plans/poc.md slice 2, ADR-0009) -------------------------------
# Libraries are folders under the read-only media mount; the admin and the API only
# ever show library-relative paths. The writable media volume holds artwork (images/)
# and, from slice 3, renditions (renditions/); the edge serves it read-only.
LIBRARY_ROOT = env("LIBRARY_ROOT", "/media")
DATA_ROOT = env("DATA_ROOT", "/data")
# inotify sees nothing on network shares (NFS, SMB): poll them instead.
LIBRARY_WATCHER_POLLING = env_bool("LIBRARY_WATCHER_POLLING", default=False)

# --- Metadata (SPEC §7.2) ------------------------------------------------------------
# Without a TMDB credential the client runs in fixture mode: synthetic sample metadata,
# offline (apps.metadata.tmdb.factory). A v4 read-access token is preferred over a key.
TMDB_API_KEY = env("TMDB_API_KEY", "")
TMDB_READ_ACCESS_TOKEN = env("TMDB_READ_ACCESS_TOKEN", "")
TMDB_LANGUAGE = env("TMDB_LANGUAGE", "en-US")
TMDB_RATE_LIMIT_PER_S = env_int("TMDB_RATE_LIMIT_PER_S", default=35)
TMDB_CACHE_TTL_S = env_int("TMDB_CACHE_TTL_S", default=24 * 60 * 60)

# --- Email (SPEC §7.7, B1/ADR-0012) -----------------------------------------------------
# SMTP for notifications. Without EMAIL_HOST nothing is sent: messages wait in the
# outbox, visible in the admin, and go out once it is set (dev: Mailpit, dev.py).
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", default=20)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", f"Smart IPTV <no-reply@{DOMAIN}>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --- Payment providers (SPEC §7.6, B1/ADR-0012) ------------------------------------------
# Sandbox or live keys; a provider is offered only with its keys set and its
# billing.<provider>_enabled setting on. Webhooks: https://api.<DOMAIN>/api/v1/webhooks/<provider>.
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", "")
MOYASAR_SECRET_KEY = env("MOYASAR_SECRET_KEY", "")
MOYASAR_WEBHOOK_SECRET = env("MOYASAR_WEBHOOK_SECRET", "")

# --- Celery -------------------------------------------------------------------
# Queues from SPEC §13. `worker` consumes the general queues; transcoders
# subscribe only to the transcode.* queue matching their hardware (M8).
# Every queue is bound under its own name on one direct exchange. (Queues declared
# without a routing key all got the key "default", so each message reached every bound
# queue. The exchange has a new name so the brokers' old bindings no longer apply.)
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_DEFAULT_EXCHANGE = "tasks"
CELERY_TASK_DEFAULT_ROUTING_KEY = "default"
_TASK_EXCHANGE = Exchange("tasks", type="direct")
CELERY_TASK_QUEUES = tuple(
    Queue(name, _TASK_EXCHANGE, routing_key=name)
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
# Ingest (SPEC §7.1-7.2): scans and probes on `scan`, matching and enrichment on
# `metadata`, artwork on `images`; all on the `worker` service.
CELERY_TASK_ROUTES = {
    "apps.library.tasks.*": {"queue": "scan"},
    # Media (ADR-0010): planning probes the source, so it runs with the scans; jobs are
    # sent to their backend's transcode.* queue explicitly (CPU if anyone forgets).
    "apps.media.tasks.prepare_media_file": {"queue": "scan"},
    "apps.media.tasks.run_transcode_job": {"queue": "transcode.cpu"},
    "apps.metadata.tasks.fetch_*": {"queue": "images"},
    "apps.metadata.tasks.*": {"queue": "metadata"},
    # Notifications (SPEC §7.7, B1/ADR-0012): delivery on the notify queue.
    "apps.notifications.tasks.*": {"queue": "notify"},
}
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
    # Customers whose access period ended: entitlement -> expired, sessions stop (M7).
    "accounts-expire-access": {
        "task": "apps.accounts.tasks.expire_access",
        "schedule": 300.0,
        "options": {"expires": 280},
    },
    # Starts the reconciliation scan of every library whose scan_interval_min has passed.
    "library-reconcile": {
        "task": "apps.library.tasks.reconcile_libraries",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    # Plans matched files that were missed and requeues jobs of dead transcoders (M8).
    "media-reconcile": {
        "task": "apps.media.tasks.reconcile_media",
        "schedule": 300.0,
        "options": {"expires": 280},
    },
    # Deletes orphaned, expired and superseded renditions (M8, ADR-0014; T1).
    "media-cleanup-renditions": {
        "task": "apps.media.tasks.cleanup_renditions",
        "schedule": 6 * 3600.0,
        "options": {"expires": 3600},
    },
    # Closes playback sessions whose heartbeat stopped and records them (SPEC §7.4).
    "playback-sweep-sessions": {
        "task": "apps.playback.tasks.sweep_sessions",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    # --- Billing and notifications (B1, ADR-0012) ---
    # Subscriptions: pending -> active -> grace -> expired, reminders, the gauge.
    "billing-advance-subscriptions": {
        "task": "apps.billing.tasks.advance_subscriptions",
        "schedule": 300.0,
        "options": {"expires": 280},
    },
    # Voids checkouts left unpaid for billing.checkout_ttl_hours.
    "billing-expire-checkouts": {
        "task": "apps.billing.tasks.expire_checkouts",
        "schedule": 3600.0,
        "options": {"expires": 3000},
    },
    # Retries due notifications; sends what waited for the SMTP settings.
    "notifications-dispatch": {
        "task": "apps.notifications.tasks.dispatch_queued",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    # --- end B1 ---
    # --- Search and recommendations (C1, ADR-0013) ---
    # Nightly: the search index rebuilt beside the live one and swapped in (SPEC §7.8).
    "search-rebuild-index": {
        "task": "apps.search.tasks.rebuild_index",
        "schedule": crontab(hour=3, minute=10),
        "options": {"expires": 3 * 3600},
    },
    # Nightly: similar titles for "more like this" and recommendations (SPEC §7.9).
    "engagement-similar-titles": {
        "task": "apps.engagement.tasks.compute_similar_titles",
        "schedule": crontab(hour=3, minute=40),
        "options": {"expires": 3 * 3600},
    },
    # --- end C1 ---
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
# Admin auth is a same-origin session cookie plus CSRF (ADR-0004). Sessions end
# after 30 idle minutes: each request saves the session, which renews its expiry
# (SPEC §8.2; security.admin_idle_timeout_min is the per-login override).
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
# The SPA reads the csrftoken cookie and sends it back as X-CSRFToken.
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "apps.core.errors.csrf_failure"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- REST API (SPEC §8.4, §10) ----------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.core.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "EXCEPTION_HANDLER": "apps.core.errors.problem_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Smart IPTV Admin API",
    "DESCRIPTION": "Same-origin API of the admin SPA. Errors are RFC 9457 problem details.",
    # The API version (the /v1 in paths), not APP_VERSION: the generated client
    # must not change when only the release number does.
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Operation ids and tags from the path after /api/v1[/admin]: settings_list, audit_list.
    "SCHEMA_PATH_PREFIX": r"/api/v1(/admin)?(?=/|$)",
    # Stable enum component names; every shared enum gets an entry here.
    "ENUM_NAME_OVERRIDES": {
        "SettingKind": "apps.core.registry.SettingKind",
        "UserStatus": "apps.accounts.models.UserStatus",
        "Locale": "apps.accounts.models.Locale",
        "MaxQuality": "apps.accounts.models.MaxQuality",
        "ConcurrencyPolicy": "apps.accounts.models.ConcurrencyPolicy",
        "DeviceKind": "apps.accounts.models.DeviceKind",
        "AppHint": "apps.accounts.models.AppHint",
        "AccessRuleType": "apps.accounts.models.AccessRuleType",
        "CategoryKind": "apps.catalog.models.CategoryKind",
        "TitleStatus": "apps.catalog.models.TitleStatus",
        "TitleVisibility": "apps.catalog.serializers.VISIBILITY_CHOICES",
        "MetadataSource": "apps.catalog.models.MetadataSource",
        "ImageKind": "apps.catalog.models.ImageKind",
        "HdrKind": "apps.catalog.models.HdrKind",
        "FileState": "apps.catalog.models.FileState",
        "ReviewStatus": "apps.catalog.models.ReviewStatus",
        "ReviewKind": "apps.catalog.models.ReviewKind",
        "CreditRole": "apps.catalog.models.CreditRole",
        "LibraryKind": "apps.library.models.LibraryKind",
        "ProcessingPolicy": "apps.library.models.ProcessingPolicy",
        "ScanTrigger": "apps.library.models.ScanTrigger",
        "ScanStatus": "apps.library.models.ScanStatus",
        "AccessStatus": "apps.playback.entitlements.EntitlementStatus",
        "DeviceStatus": "apps.accounts.serializers.DeviceStatus",
        "LoginStatus": "apps.accounts.auth.LoginStatus",
        # C1 (ADR-0013): movie-or-series choices (collections, customer API).
        "TitleType": "apps.catalog.serializers_customer.TITLE_TYPE_CHOICES",
        # Billing and notifications (B1, ADR-0012).
        "SubscriptionStatus": "apps.billing.models.SubscriptionStatus",
        "SubscriptionSource": "apps.billing.models.SubscriptionSource",
        "SubscriptionEndReason": "apps.billing.models.EndReason",
        "InvoiceStatus": "apps.billing.models.InvoiceStatus",
        "PaymentProviderCode": "apps.billing.models.PaymentProviderCode",
        "PaymentStatus": "apps.billing.models.PaymentStatus",
        "PaymentMethod": "apps.billing.models.PaymentMethod",
        "NotificationChannel": "apps.notifications.models.Channel",
        "OutboxStatus": "apps.notifications.models.OutboxStatus",
        # Admin system health (ADR-0015).
        "HealthStatus": "apps.dashboard.health.Status",
        # Media pipeline (T1, ADR-0014); TranscodeJobStatusEnum keeps its generated name.
        "TranscodeJobStatusEnum": "apps.media.models.JobStatus",
        "TranscodeProfile": "apps.media.models.TranscodeProfile",
        "RenditionKind": "apps.media.models.RenditionKind",
        "RenditionStatus": "apps.media.models.RenditionStatus",
        "SubtitleStatus": "apps.media.models.SubtitleStatus",
        "SubtitleFormat": "apps.media.models.SubtitleFormat",
    },
    "ENUM_ADD_EXPLICIT_BLANK_NULL_CHOICE": False,
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "apps.core.schema.add_problem_details",
    ],
}

# --- Metrics (SPEC §14) ----------------------------------------------------------------
# Migration gauges would query the database whenever Django starts.
PROMETHEUS_EXPORT_MIGRATIONS = False

# --- Admin system health (SPEC §8.3 System Health, ADR-0015) ---------------------------
# Edge health URLs the Health page probes from inside the backend network (comma-separated),
# and the Grafana base URL for its "Open in Grafana" links (empty: no links).
EDGE_HEALTH_URLS = env_list("EDGE_HEALTH_URLS", ["http://nginx-stream:8080/healthz"])
GRAFANA_URL = env("GRAFANA_URL", "")

# --- Logging (SPEC §11, §14) -------------------------------------------------------------
# structlog for everything, redacted, on stderr. JSON by default; dev.py picks the
# console renderer. DJANGO_LOG_FORMAT overrides either.
LOG_LEVEL = env("DJANGO_LOG_LEVEL", "INFO")
LOG_FORMAT = parse_log_format(env("DJANGO_LOG_FORMAT", "json"))
LOGGING = logging_config(level=LOG_LEVEL, log_format=LOG_FORMAT)
configure_structlog()
