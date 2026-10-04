from django.apps import AppConfig


class MediaConfig(AppConfig):
    name = "apps.media"
    label = "media"

    def ready(self) -> None:
        # Connects the matched-file receiver and the transcoder's Celery signals.
        from apps.media import receivers, worker  # noqa: F401, PLC0415
