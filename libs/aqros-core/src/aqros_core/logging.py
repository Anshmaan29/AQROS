"""Structured logging configuration.

Console-rendered logs in development; JSON logs (``AQROS_LOG_JSON=true``) in
staging/production so they are machine-parseable by the log pipeline. Every
logger is bound with the service name and environment for correlation.
"""

from __future__ import annotations

import logging
import sys

import structlog

from aqros_core.config import BaseServiceSettings


def configure_logging(settings: BaseServiceSettings) -> structlog.stdlib.BoundLogger:
    """Configure structlog + stdlib logging and return a bound service logger."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logger: structlog.stdlib.BoundLogger = structlog.get_logger(settings.service_name)
    return logger.bind(service=settings.service_name, env=settings.environment.value)
