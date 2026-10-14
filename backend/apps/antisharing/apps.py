from django.apps import AppConfig


class AntisharingConfig(AppConfig):
    """Anti-sharing: networks and countries seen per customer, the risk score, flags and
    the admin's escalation ladder (SPEC §11.1; G-27, ADR-0021)."""

    name = "apps.antisharing"
    label = "antisharing"
    verbose_name = "Anti-sharing"

    def ready(self) -> None:
        from apps.antisharing import receivers  # noqa: F401, PLC0415 (connects signals)
