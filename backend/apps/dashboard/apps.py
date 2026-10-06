from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "apps.dashboard"
    label = "dashboard"

    def ready(self) -> None:
        # Heartbeat ages and queue lengths, read while /metrics is scraped (ADR-0018).
        from apps.core.metrics import register_scrape_collector  # noqa: PLC0415
        from apps.dashboard.metrics import LiveStateCollector  # noqa: PLC0415

        register_scrape_collector(LiveStateCollector())
