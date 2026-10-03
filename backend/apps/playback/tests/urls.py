"""The admin URLconf with the playback routes mounted, for this app's tests and its
schema check, until config.urls_admin includes apps.playback.urls_admin itself."""

from django.urls import URLResolver, include, path

from config import urls_admin

PLAYBACK_ROUTES = "apps.playback.urls_admin"


def _mounted() -> bool:
    for pattern in urls_admin.admin_api:
        module = pattern.urlconf_name if isinstance(pattern, URLResolver) else None
        if getattr(module, "__name__", module) == PLAYBACK_ROUTES:
            return True
    return False


urlpatterns = list(urls_admin.urlpatterns)
if not _mounted():
    urlpatterns.insert(0, path("api/v1/admin/", include(PLAYBACK_ROUTES)))

handler400 = urls_admin.handler400
handler403 = urls_admin.handler403
handler404 = urls_admin.handler404
handler500 = urls_admin.handler500
