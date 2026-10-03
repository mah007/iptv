import time

from celery import shared_task

from apps.core.stores import state_redis

HEARTBEAT_KEY = "hb:beat"
HEARTBEAT_TTL_S = 300


@shared_task(name="apps.core.tasks.heartbeat", ignore_result=True)
def heartbeat() -> None:
    """Scheduled by beat every 30 s and run by a worker.

    A fresh key proves the whole beat → broker → worker path works; the beat
    container's healthcheck reads it.
    """
    state_redis().set(HEARTBEAT_KEY, int(time.time()), ex=HEARTBEAT_TTL_S)
