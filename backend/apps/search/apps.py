from django.apps import AppConfig


class SearchConfig(AppConfig):
    name = "apps.search"
    label = "search"

    def ready(self) -> None:
        from apps.catalog.signals import catalog_changed  # noqa: PLC0415 (models loaded)
        from apps.search.tasks import on_catalog_changed  # noqa: PLC0415

        # Every catalogue change reaches the search index (SPEC §7.8, ADR-0013).
        catalog_changed.connect(on_catalog_changed, dispatch_uid="search-index")
