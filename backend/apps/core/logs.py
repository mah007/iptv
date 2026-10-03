"""One logging pipeline for structlog and stdlib loggers (SPEC §14).

Every record, whether it comes from structlog or from a stdlib logger (Django,
Celery, libraries), runs through the same processors and ends in one handler on
stderr. JSON in production, a readable console format in development.

The redaction processor runs last before rendering, after exception formatting,
so tracebacks and anything bound through contextvars are redacted too (SPEC §11).

Logs go to stderr only: stdout stays clean for command output such as
`manage.py spectacular`, which `make api-client` writes to a file.
"""

from typing import Any, Literal

import structlog
from django.core.exceptions import ImproperlyConfigured
from structlog.typing import Processor

from apps.core.redaction import redact_event_dict

type LogFormat = Literal["json", "console"]


def parse_log_format(value: str) -> LogFormat:
    """Validate DJANGO_LOG_FORMAT."""
    if value == "json":
        return "json"
    if value == "console":
        return "console"
    msg = f"DJANGO_LOG_FORMAT must be json or console, got {value!r}"
    raise ImproperlyConfigured(msg)


def shared_processors() -> list[Processor]:
    """Processors applied to every record before it is rendered."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_event_dict,
    ]


def configure_structlog() -> None:
    """Route structlog through stdlib logging, so both share handlers and formatting."""
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def renderer(log_format: LogFormat) -> Processor:
    if log_format == "json":
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer()


def logging_config(*, level: str, log_format: LogFormat) -> dict[str, Any]:
    """Django LOGGING: one stderr handler whose formatter renders every record."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structlog": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    renderer(log_format),
                ],
                "foreign_pre_chain": shared_processors(),
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": "structlog",
            },
        },
        "root": {"handlers": ["console"], "level": level},
        # Django's DEFAULT_LOGGING gives these loggers their own plain, unredacted
        # handlers (printing raw paths in DEBUG). Configuring them here removes
        # those handlers, so their records only reach the root handler above.
        "loggers": {
            "django": {"level": level, "propagate": True},
            "django.server": {"level": level, "propagate": True},
        },
    }
