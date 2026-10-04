"""Local development: the docker compose dev stack with hot reload."""

from apps.core.logs import logging_config, parse_log_format
from config.env import env, env_bool, env_int

from .base import *

DEBUG = True

# Readable logs in `make logs`; DJANGO_LOG_FORMAT=json shows what production emits.
LOG_FORMAT = parse_log_format(env("DJANGO_LOG_FORMAT", "console"))
LOGGING = logging_config(level=LOG_LEVEL, log_format=LOG_FORMAT)

# The browsable API, for poking at endpoints from a browser while developing.
# A new dict: the star import shares base's object with every other settings module.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# --- Email (B1, ADR-0012) ---
# Mailpit (docker/compose.dev.yml) catches every message; its UI is http://mail.<DOMAIN>.
EMAIL_HOST = env("EMAIL_HOST", "mailpit")
EMAIL_PORT = env_int("EMAIL_PORT", default=1025)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=False)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
