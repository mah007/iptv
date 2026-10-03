from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self) -> None:
        # Registers the OpenAPI extension for our session authentication class.
        from apps.core import schema  # noqa: F401, PLC0415
