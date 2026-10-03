"""Production. TLS terminates at Traefik, which sets X-Forwarded-Proto."""

from config.env import env, env_bool, env_int
from config.origins import default_port, origins

from .base import *

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# Internal endpoints are reached over plain HTTP inside the Docker network.
SECURE_REDIRECT_EXEMPT = [r"^internal/", r"^metrics$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", default=31_536_000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", default=False)
if not SECURE_HSTS_PRELOAD:
    # Preload is the operator's explicit choice (DJANGO_HSTS_PRELOAD=1): removing a
    # domain from the browsers' preload list takes months.
    SILENCED_SYSTEM_CHECKS = ["security.W021"]

# Browsers reach the admin SPA and the portal over https (ADR-0004).
PUBLIC_SCHEME = env("PUBLIC_SCHEME", "https")
PUBLIC_PORT = env_int("PUBLIC_PORT", default=default_port(PUBLIC_SCHEME))
CSRF_TRUSTED_ORIGINS = origins(PUBLIC_SCHEME, [ADMIN_HOST, APP_HOST], PUBLIC_PORT)
