from django.apps import AppConfig


class LiveConfig(AppConfig):
    name = "apps.live"
    label = "live"
    verbose_name = "Live TV"

    def ready(self) -> None:
        from apps.live.xtream import DjangoLiveSource  # noqa: PLC0415
        from apps.xtream_api.live_source import set_live_source  # noqa: PLC0415

        set_live_source(DjangoLiveSource())
