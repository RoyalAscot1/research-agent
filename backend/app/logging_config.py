"""Structured logging setup for the backend.

One JSON line per event, emitted to stdout (Render captures stdout into its log
stream). Every line carries any context bound via `structlog.contextvars` — most
importantly the `job_id`, bound once at the top of a research run so the whole
planner -> researcher -> coverage -> sentiment -> synthesizer -> DB-write timeline
is filterable end to end.

Event-name convention: dotted lowercase identifiers (`researcher.complete`,
`auth.rejected`) as the stable, greppable key; variable detail goes in fields so it
stays queryable rather than buried in a prose message.

Complements the live Langfuse tracing rather than duplicating it: Langfuse captures
LLM-call traces; these logs cover the non-LLM operational surface (DB writes, auth,
Tavily/YouTube failures, request lifecycle) — what you actually grep when something
breaks.
"""

import logging
import sys

import structlog

from app.config import settings


def configure_logging() -> None:
    """Configure structlog + stdlib logging to emit JSON to stdout.

    Idempotent-ish: safe to call once at startup. Routes stdlib logging (uvicorn,
    SQLAlchemy, etc.) through the same JSON renderer so the log stream is uniform.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn access/error, SQLAlchemy, etc.) through the same
    # JSON pipeline so the stream isn't a mix of JSON and plain text.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers; clear them so lines aren't double-emitted.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger. Pass `__name__` from the calling module."""
    return structlog.get_logger(name)


def bind_job_id(job_id: str) -> None:
    """Bind `job_id` to the context so every subsequent log line in this run carries it.

    Called at the top of `run_graph`. Because LangGraph runs each job in its own task,
    contextvars keep the binding isolated per run. Pair with `clear_log_context()` when
    the run ends.
    """
    structlog.contextvars.bind_contextvars(job_id=job_id)


def clear_log_context() -> None:
    """Clear any context bound for the current task (e.g. `job_id`)."""
    structlog.contextvars.clear_contextvars()
