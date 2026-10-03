"""Local development: the docker compose dev stack with hot reload."""

from apps.core.logs import logging_config, parse_log_format
from config.env import env

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
