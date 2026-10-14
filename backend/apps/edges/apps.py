from typing import Any

from django.apps import AppConfig
from django.db.models.signals import m2m_changed, post_delete, post_save


class EdgesConfig(AppConfig):
    """Media edges: the local one, standalone edges and CDN zones (ADR-0022)."""

    name = "apps.edges"
    label = "edges"
    verbose_name = "Edges"

    def ready(self) -> None:
        from apps.core.metrics import register_scrape_collector  # noqa: PLC0415
        from apps.edges.metrics import EdgeCollector  # noqa: PLC0415
        from apps.edges.models import Edge  # noqa: PLC0415

        register_scrape_collector(EdgeCollector())
        post_save.connect(_edge_changed, sender=Edge, dispatch_uid="edges-saved")
        post_delete.connect(_edge_changed, sender=Edge, dispatch_uid="edges-deleted")
        m2m_changed.connect(
            _edge_changed, sender=Edge.canary_users.through, dispatch_uid="edges-canary"
        )


def _edge_changed(**kwargs: Any) -> None:
    from apps.edges import services  # noqa: PLC0415

    services.changed()
