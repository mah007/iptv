"""pytest settings. Tests run inside the dev stack (`make test`), which supplies
the real hosts and credentials; the defaults below only let these settings
import without a .env (host-side mypy loads them via django-stubs).

Redis tests use their own database indexes so a test run never touches the
data of a running dev stack. Runs in parallel (`make test-backend lane=N`) each get
a lane: their own Postgres test database and Redis indexes, so one run never drops
or flushes another's data.
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
# A valid Fernet key (urlsafe base64 of 32 bytes) for host-side tooling such as mypy.
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "dGVzdC1vbmx5LWluc2VjdXJlLWZlcm5ldC1rZXkhISE=")

from .base import *


def _with_db(url: str, db: int) -> str:
    return urlsplit(url)._replace(path=f"/{db}").geturl()


# Lane 0 (the default) uses Redis indexes 13-15, lane 1 10-12, lane 2 7-9 and
# lane 3 4-6; the dev stack itself uses 0 and 1.
TEST_LANE = int(os.environ.get("TEST_LANE") or 0)
if not 0 <= TEST_LANE <= 3:
    msg = f"TEST_LANE must be 0-3, not {TEST_LANE}"
    raise ValueError(msg)
_REDIS_DB = 13 - 3 * TEST_LANE

REDIS_STATE_URL = _with_db(REDIS_STATE_URL, _REDIS_DB)
CELERY_BROKER_URL = _with_db(CELERY_BROKER_URL, _REDIS_DB + 1)
REDIS_CACHE_URL = _with_db(REDIS_CACHE_URL, _REDIS_DB + 2)
CACHES["default"]["LOCATION"] = REDIS_CACHE_URL
if TEST_LANE:
    DATABASES["default"]["TEST"] = {"NAME": f"test_{DATABASES['default']['NAME']}_lane{TEST_LANE}"}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
