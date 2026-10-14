"""Fixtures shared by every app's tests."""

from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import Permission, Role, User
from apps.accounts.rbac import OWNER_ROLE, sync_rbac
from apps.catalog.models import Category, CategoryKind
from apps.core.services import reset_settings_cache
from apps.library.models import Library
from apps.metadata.tmdb import TMDBClient

type AdminFactory = Callable[..., User]
type CustomerFactory = Callable[..., User]


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> Iterator[None]:
    """Settings overrides are cached in redis-cache under a version key; each test
    rolls its database back, so it must also start (and end) with an empty cache."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _fresh_edges_and_replica_state() -> Iterator[None]:
    """The edges' snapshot (redis-cache, plus a 2 s in-process memo) and the replica's
    measured lag outlive a test's rolled-back rows: each test starts without them
    (M15, ADR-0022)."""
    from apps.core import replica  # noqa: PLC0415
    from apps.edges import services as edges  # noqa: PLC0415

    def forget() -> None:
        edges.reset_memo()
        replica.reset()
        try:
            from apps.core.stores import cache_redis  # noqa: PLC0415

            cache_redis().delete(edges.SNAPSHOT_KEY, replica.LAG_KEY)
        except Exception:  # noqa: S110 (no store: nothing cached)
            pass

    forget()
    yield
    forget()


@pytest.fixture
def no_server_config(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """No setting falls back to `.env` (the dev stack's environment reaches the tests):
    every registry `env` name is unset in Django's settings and the environment."""
    from apps.core import registry  # noqa: PLC0415

    for definition in registry.definitions():
        for name in definition.env:
            monkeypatch.delenv(name, raising=False)
            if hasattr(settings, name):
                setattr(settings, name, None)


@pytest.fixture(autouse=True)
def _step_up_confirmed(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests are about what a sensitive endpoint does, not the confirmation in front
    of it (ADR-0021): they run as if the admin or customer had just confirmed. Tests
    marked `@pytest.mark.step_up` get the real check."""
    if request.node.get_closest_marker("step_up") is None:
        monkeypatch.setattr("apps.accounts.stepup.is_elevated", lambda _request: True)


@pytest.fixture
def geoip_dir(settings: Any, tmp_path: Path) -> Iterator[Path]:
    """An empty GeoIP folder for this test (no database, no download)."""
    from apps.core import geoip  # noqa: PLC0415

    folder = tmp_path / "geoip"
    folder.mkdir()
    settings.GEOIP_DIR = str(folder)
    geoip.reset_readers()
    yield folder
    geoip.reset_readers()


@pytest.fixture(autouse=True)
def _clean_log_context() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def staff_user(db: None) -> User:
    """An admin holding the owner role, so every permission."""
    sync_rbac()
    user: User = get_user_model().objects.create_user(
        username="staff",
        email="staff@example.com",
        password="staff-pass-123",  # noqa: S106
        is_staff=True,
    )
    user.roles.add(Role.objects.get(name=OWNER_ROLE))
    return user


@pytest.fixture
def customer_user(db: None) -> User:
    return get_user_model().objects.create_user(
        username="customer",
        email="customer@example.com",
        password="customer-pass-123",  # noqa: S106
    )


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def rbac(db: None) -> None:
    sync_rbac()


@pytest.fixture
def make_admin(rbac: None) -> AdminFactory:
    """Staff users with the given seed roles, or with a custom role of `permissions`."""
    counter = iter(range(1, 10_000))

    def factory(
        *roles: str, permissions: Iterable[str] | None = None, username: str | None = None
    ) -> User:
        user = User.objects.create_user(
            username=username or f"admin{next(counter)}",
            password="admin-pass-1234",  # noqa: S106
            is_staff=True,
        )
        user.roles.set(Role.objects.filter(name__in=roles))
        if permissions is not None:
            role = Role.objects.create(name=f"custom_{user.username}")
            role.permissions.set(Permission.objects.filter(code__in=list(permissions)))
            user.roles.add(role)
        return user

    return factory


@pytest.fixture
def owner(make_admin: AdminFactory) -> User:
    return make_admin("owner", username="owner")


@pytest.fixture
def owner_client(owner: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(owner)
    return client


@pytest.fixture
def make_customer(db: None) -> CustomerFactory:
    """Customers created through the service, as the admin API creates them."""
    counter = iter(range(1, 10_000))

    def factory(
        *,
        name: str | None = None,
        expires_at: datetime | None = None,
        devices: int = 0,
        **access: Any,
    ) -> User:
        number = next(counter)
        user, _issued = services.create_customer(
            {"name": name or f"Customer {number}", "email": f"customer{number}@example.com"},
            access={"expires_at": expires_at, **access},
            actor=None,
        )
        for index in range(devices):
            services.create_device_credential(user, name=f"Device {index + 1}", actor=None)
        return user

    return factory


@pytest.fixture
def category(db: None) -> Category:
    return Category.objects.create(
        kind=CategoryKind.VOD, slug="action", name_en="Action", name_ar="أكشن"
    )


# --- Libraries, media files and metadata (POC slice 2) -----------------------------------------


@pytest.fixture
def media_root(settings: Any, tmp_path: Path) -> Path:
    """An empty media root with `movies` and `series` folders (settings.LIBRARY_ROOT)."""
    root = tmp_path / "media"
    (root / "movies").mkdir(parents=True)
    (root / "series").mkdir()
    settings.LIBRARY_ROOT = str(root)
    return root


@pytest.fixture
def data_root(settings: Any, tmp_path: Path) -> Path:
    """An empty media volume (settings.DATA_ROOT) for artwork."""
    root = tmp_path / "data"
    root.mkdir()
    settings.DATA_ROOT = str(root)
    return root


@pytest.fixture
def make_library(media_root: Path) -> Callable[..., Library]:
    """Libraries over folders of the temporary media root."""

    def factory(name: str = "Movies", kind: str = "movies", folder: str = "movies") -> Library:
        path = media_root / folder
        path.mkdir(parents=True, exist_ok=True)
        return Library.objects.create(name=name, kind=kind, path=str(path))

    return factory


@pytest.fixture
def offline_tmdb(monkeypatch: pytest.MonkeyPatch) -> Iterator[TMDBClient]:
    """Fixture mode for the metadata services, whatever TMDB credential the env holds."""
    from apps.metadata import services as metadata  # noqa: PLC0415

    from apps.metadata import tvdb_series  # noqa: PLC0415

    client = TMDBClient.offline()
    metadata.reset_caches()
    monkeypatch.setattr(metadata, "tmdb", lambda: client)
    # No TheTVDB fallback unless a test asks for it (`offline_tvdb`), whatever key the
    # env holds: tests never reach the network.
    monkeypatch.setattr(tvdb_series, "tvdb", lambda: None)
    yield client
    monkeypatch.undo()
    metadata.reset_caches()


@pytest.fixture
def offline_tvdb(offline_tmdb: TMDBClient, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """TheTVDB's fallback over its synthetic fixtures (ADR-0023), with TMDB offline too."""
    from apps.metadata import tvdb_series  # noqa: PLC0415
    from apps.metadata.tvdb import TVDBClient  # noqa: PLC0415

    client = TVDBClient.offline()
    monkeypatch.setattr(tvdb_series, "tvdb", lambda: client)
    yield client
    client.close()
