"""pytest settings. Tests run inside the dev stack (`make test`), which supplies
the real hosts and credentials; the defaults below only let these settings
import without a .env (host-side mypy loads them via django-stubs).

Redis tests use their own database indexes so a test run never touches the
data of a running dev stack.
"""

import os
from urllib.parse import urlsplit

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-insecure-key")
os.environ.setdefault("POSTGRES_DB", "iptv")
os.environ.setdefault("POSTGRES_USER", "iptv")
os.environ.setdefault("POSTGRES_PASSWORD", "iptv")
os.environ.setdefault("REDIS_STATE_PASSWORD", "iptv")
os.environ.setdefault("REDIS_CACHE_PASSWORD", "iptv")
os.environ.setdefault("MEILI_MASTER_KEY", "test-only-master-key")

from .base import *


def _with_db(url: str, db: int) -> str:
    return urlsplit(url)._replace(path=f"/{db}").geturl()


REDIS_STATE_URL = _with_db(REDIS_STATE_URL, 13)
CELERY_BROKER_URL = _with_db(CELERY_BROKER_URL, 14)
REDIS_CACHE_URL = _with_db(REDIS_CACHE_URL, 15)
CACHES["default"]["LOCATION"] = REDIS_CACHE_URL

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
