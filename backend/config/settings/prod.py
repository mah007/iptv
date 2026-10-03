"""Production. TLS terminates at Traefik, which sets X-Forwarded-Proto."""

from config.env import env_bool, env_int

from .base import *

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# Internal endpoints are reached over plain HTTP inside the Docker network.
SECURE_REDIRECT_EXEMPT = [r"^internal/"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", default=31_536_000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", default=False)
if not SECURE_HSTS_PRELOAD:
    # Preload is the operator's explicit choice (DJANGO_HSTS_PRELOAD=1): removing a
    # domain from the browsers' preload list takes months.
    SILENCED_SYSTEM_CHECKS = ["security.W021"]
