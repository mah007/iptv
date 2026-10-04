from django.apps import AppConfig


class XtreamApiConfig(AppConfig):
    name = "apps.xtream_api"
    label = "xtream_api"

    def ready(self) -> None:
        """Serve the real catalog and playback; tests swap in fakes with the setters."""
        from apps.xtream_api.catalog import DjangoCatalogSource  # noqa: PLC0415
        from apps.xtream_api.playback import set_playback_starter  # noqa: PLC0415
        from apps.xtream_api.source import set_catalog_source  # noqa: PLC0415
        from apps.xtream_api.starter import DjangoPlaybackStarter  # noqa: PLC0415

        set_catalog_source(DjangoCatalogSource())
        set_playback_starter(DjangoPlaybackStarter())
