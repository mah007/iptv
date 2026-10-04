from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "apps.catalog"
    label = "catalog"

    def ready(self) -> None:
        from apps.catalog import home  # noqa: PLC0415 (models must be loaded)
        from apps.catalog.signals import catalog_changed  # noqa: PLC0415

        # Cached home rows go stale with any catalogue change (ADR-0013).
        catalog_changed.connect(home.invalidate_on_commit, dispatch_uid="catalog-home-cache")
