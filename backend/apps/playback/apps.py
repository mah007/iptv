from django.apps import AppConfig


class PlaybackConfig(AppConfig):
    name = "apps.playback"
    label = "playback"

    def ready(self) -> None:
        from apps.playback import receivers  # noqa: F401, PLC0415 (connects the receivers)
