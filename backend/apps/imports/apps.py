from django.apps import AppConfig


class ImportsConfig(AppConfig):
    """Imports from storage the operator controls, or from one direct link (ADR-0025)."""

    name = "apps.imports"
    label = "imports"
    verbose_name = "Imports"
