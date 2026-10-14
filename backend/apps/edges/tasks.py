"""Edge jobs on the worker (ADR-0022)."""

from celery import shared_task

from apps.edges import services


@shared_task(name="apps.edges.tasks.probe_local", ignore_result=True)
def probe_local() -> bool:
    """The local edge's /healthz and stub_status, every 15 s: its health for selection."""
    return bool(services.probe_local()["ok"])


@shared_task(name="apps.edges.tasks.rebuild_auth", ignore_result=True)
def rebuild_auth() -> int:
    """The bearer hashes in redis-state, rebuilt from Postgres every minute."""
    return services.rebuild_auth_mirror()
